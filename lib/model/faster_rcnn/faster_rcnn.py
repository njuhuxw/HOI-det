import random
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torchvision.models as models
from torch.autograd import Variable
import numpy as np
from model.utils.config import cfg
from model.rpn.rpn import _RPN
from model.roi_pooling.modules.roi_pool import _RoIPooling
from model.roi_crop.modules.roi_crop import _RoICrop
from model.roi_align.modules.roi_align import RoIAlignAvg
from model.rpn.proposal_target_layer_cascade import _ProposalTargetLayer
import time
import pdb
from model.utils.net_utils import _smooth_l1_loss, _crop_pool_layer, _affine_grid_gen, _affine_theta

class _fasterRCNN(nn.Module):
    """ faster RCNN """
    def __init__(self, classes, class_agnostic):
        super(_fasterRCNN, self).__init__()
        self.classes = classes
        self.n_classes = len(classes)
        self.class_agnostic = class_agnostic

        # define rpn
        # self.RCNN_rpn = _RPN(self.dout_base_model)
        # self.RCNN_proposal_target = _ProposalTargetLayer(self.n_classes)
        self.RCNN_roi_pool = _RoIPooling(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0/16.0)
        self.RCNN_roi_align = RoIAlignAvg(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0/16.0)

        self.grid_size = cfg.POOLING_SIZE * 2 if cfg.CROP_RESIZE_WITH_MAX_POOL else cfg.POOLING_SIZE
        self.RCNN_roi_crop = _RoICrop()

    def forward(self, im_data, im_info, hboxes, oboxes, iboxes, hoi_classes, bin_classes, num_hois):
        batch_size = im_data.size(0)

        im_info = im_info.data
        hboxes = hboxes.data
        oboxes = oboxes.data
        iboxes = iboxes.data
        num_hois = num_hois.data

        # feed image data to base model to obtain base feature map
        base_feat = self.RCNN_base(im_data)

        hrois = Variable(torch.zeros(hboxes.shape[0], hboxes.shape[1], hboxes.shape[2] + 1))
        orois = Variable(torch.zeros(oboxes.shape[0], oboxes.shape[1], oboxes.shape[2] + 1))
        irois = Variable(torch.zeros(iboxes.shape[0], iboxes.shape[1], iboxes.shape[2] + 1))

        if im_data.is_cuda:
            hrois = hrois.cuda()
            orois = orois.cuda()
            irois = irois.cuda()

        hrois[:, :, 1:] = hboxes
        orois[:, :, 1:] = oboxes
        irois[:, :, 1:] = iboxes
        
        # rois = Variable(rois)
        # do roi pooling based on predicted rois

        if cfg.POOLING_MODE == 'crop':
            # pdb.set_trace()
            # pooled_feat_anchor = _crop_pool_layer(base_feat, rois.view(-1, 5))
            grid_xy = _affine_grid_gen(irois.view(-1, 5), base_feat.size()[2:], self.grid_size)
            grid_yx = torch.stack([grid_xy.data[:,:,:,1], grid_xy.data[:,:,:,0]], 3).contiguous()
            pooled_feat = self.RCNN_roi_crop(base_feat, Variable(grid_yx).detach())
            if cfg.CROP_RESIZE_WITH_MAX_POOL:
                pooled_feat = F.max_pool2d(pooled_feat, 2, 2)
        elif cfg.POOLING_MODE == 'align':
            pooled_feat = self.RCNN_roi_align(base_feat, irois.view(-1, 5))
        elif cfg.POOLING_MODE == 'pool':
            pooled_feat = self.RCNN_roi_pool(base_feat, irois.view(-1, 5))

        # feed pooled features to top model
        pooled_feat = self._head_to_tail(pooled_feat)

        # compute bbox offset
        # bbox_pred = self.RCNN_bbox_pred(pooled_feat)
        # if self.training and not self.class_agnostic:
        #     # select the corresponding columns according to roi labels
        #     bbox_pred_view = bbox_pred.view(bbox_pred.size(0), int(bbox_pred.size(1) / 4), 4)
        #     bbox_pred_select = torch.gather(bbox_pred_view, 1, rois_label.view(rois_label.size(0), 1, 1).expand(rois_label.size(0), 1, 4))
        #     bbox_pred = bbox_pred_select.squeeze(1)

        # compute object classification probability
        cls_score = self.RCNN_cls_score(pooled_feat)
        bin_score = self.RCNN_bin_score(pooled_feat)

        bin_prob = F.softmax(bin_score, 1)
        cls_prob = F.sigmoid(cls_score)

        RCNN_loss_cls = -1
        RCNN_loss_bin = -1

        if self.training:
            # classification loss
            RCNN_loss_cls = F.binary_cross_entropy(cls_prob, hoi_classes.view(-1, hoi_classes.shape[2]))
            RCNN_loss_bin = F.cross_entropy(bin_score, bin_classes.view(bin_classes.shape[1]))

        cls_prob = cls_prob.view(batch_size, irois.size(1), -1)
        bin_prob = bin_prob.view(batch_size, irois.size(1), -1)

        return cls_prob, bin_prob, RCNN_loss_cls, RCNN_loss_bin

    def _init_weights(self):
        def normal_init(m, mean, stddev, truncated=False):
            """
            weight initalizer: truncated normal and random normal.
            """
            # x is a parameter
            if truncated:
                m.weight.data.normal_().fmod_(2).mul_(stddev).add_(mean) # not a perfect approximation
            else:
                m.weight.data.normal_(mean, stddev)
                m.bias.data.zero_()

        normal_init(self.RCNN_cls_score, 0, 0.01, cfg.TRAIN.TRUNCATED)
        normal_init(self.RCNN_bin_score, 0, 0.01, cfg.TRAIN.TRUNCATED)

    def create_architecture(self):
        self._init_modules()
        self._init_weights()
