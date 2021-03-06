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


class SpaConv(nn.Module):
    def __init__(self):
        super(SpaConv, self).__init__()
        # (batch,64,64,2)->(batch,60,60,64)
        self.conv1 = nn.Conv2d(in_channels=2, out_channels=64, kernel_size=5)
        # (batch,60,60,64)->(batch,30,30,64)
        self.pool1 = nn.MaxPool2d(kernel_size=2, stride=2)
        # (batch,30,30,64)->(batch,26,26,32)
        self.conv2 = nn.Conv2d(in_channels=64, out_channels=32, kernel_size=5)
        # (batch,26,26,32)->(batch,13,13,32)
        self.pool2 = nn.MaxPool2d(kernel_size=2, stride=2)

        self.hidden = nn.Sequential(
            nn.LeakyReLU(),
            nn.Dropout(p=0.5),
            nn.Linear(5408, 1024))

    def forward(self, spa_map):
        conv1 = self.conv1(spa_map)
        pool1 = self.pool1(conv1)
        conv2 = self.conv2(pool1)
        pool2 = self.pool2(conv2)
        pool_feat = pool2.view(spa_map.shape[0], -1)
        return self.hidden(pool_feat)


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
        self.RCNN_roi_pool = _RoIPooling(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0 / 16.0)
        self.RCNN_roi_align = RoIAlignAvg(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0 / 16.0)

        self.grid_size = cfg.POOLING_SIZE * 2 if cfg.CROP_RESIZE_WITH_MAX_POOL else cfg.POOLING_SIZE
        self.RCNN_roi_crop = _RoICrop()
        self.spa_cls_CNN = SpaConv()
        self.spa_bin_CNN = SpaConv()



    def forward(self, im_data, im_info, hboxes, oboxes, iboxes, hoi_classes, bin_classes, spa_maps, num_hois):
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

        # do roi pooling based on predicted rois
        if cfg.POOLING_MODE == 'crop':
            # pdb.set_trace()
            # pooled_feat_anchor = _crop_pool_layer(base_feat, rois.view(-1, 5))
            grid_xy = _affine_grid_gen(irois.view(-1, 5), base_feat.size()[2:], self.grid_size)
            grid_yx = torch.stack([grid_xy.data[:, :, :, 1], grid_xy.data[:, :, :, 0]], 3).contiguous()
            iroi_pooled_feat = self.RCNN_roi_crop(base_feat, Variable(grid_yx).detach())
            if cfg.CROP_RESIZE_WITH_MAX_POOL:
                iroi_pooled_feat = F.max_pool2d(iroi_pooled_feat, 2, 2)
        elif cfg.POOLING_MODE == 'align':
            iroi_pooled_feat = self.RCNN_roi_align(base_feat, irois.view(-1, 5))
        elif cfg.POOLING_MODE == 'pool':
            iroi_pooled_feat = self.RCNN_roi_pool(base_feat, irois.view(-1, 5))

        # feed pooled features to top  model
        iroi_pooled_feat = self._head_to_tail(iroi_pooled_feat)

        if cfg.POOLING_MODE == 'crop':
            # pdb.set_trace()
            # pooled_feat_anchor = _crop_pool_layer(base_feat, rois.view(-1, 5))
            grid_xy = _affine_grid_gen(hrois.view(-1, 5), base_feat.size()[2:], self.grid_size)
            grid_yx = torch.stack([grid_xy.data[:, :, :, 1], grid_xy.data[:, :, :, 0]], 3).contiguous()
            hroi_pooled_feat = self.RCNN_roi_crop(base_feat, Variable(grid_yx).detach())
            if cfg.CROP_RESIZE_WITH_MAX_POOL:
                hroi_pooled_feat = F.max_pool2d(hroi_pooled_feat, 2, 2)
        elif cfg.POOLING_MODE == 'align':
            hroi_pooled_feat = self.RCNN_roi_align(base_feat, hrois.view(-1, 5))
        elif cfg.POOLING_MODE == 'pool':
            hroi_pooled_feat = self.RCNN_roi_pool(base_feat, hrois.view(-1, 5))

        # feed pooled features to top  model
        hroi_pooled_feat = self._head_to_tail(hroi_pooled_feat)

        if cfg.POOLING_MODE == 'crop':
            # pdb.set_trace()
            # pooled_feat_anchor = _crop_pool_layer(base_feat, rois.view(-1, 5))
            grid_xy = _affine_grid_gen(orois.view(-1, 5), base_feat.size()[2:], self.grid_size)
            grid_yx = torch.stack([grid_xy.data[:, :, :, 1], grid_xy.data[:, :, :, 0]], 3).contiguous()
            oroi_pooled_feat = self.RCNN_roi_crop(base_feat, Variable(grid_yx).detach())
            if cfg.CROP_RESIZE_WITH_MAX_POOL:
                oroi_pooled_feat = F.max_pool2d(oroi_pooled_feat, 2, 2)
        elif cfg.POOLING_MODE == 'align':
            oroi_pooled_feat = self.RCNN_roi_align(base_feat, orois.view(-1, 5))
        elif cfg.POOLING_MODE == 'pool':
            oroi_pooled_feat = self.RCNN_roi_pool(base_feat, orois.view(-1, 5))

        # feed pooled features to top  model
        oroi_pooled_feat = self._head_to_tail(oroi_pooled_feat)

        spa_feat = self.spa_cls_CNN(spa_maps[0])
        scls_score = self.spa_cls_score(spa_feat)
        scls_prob = F.sigmoid(scls_score)
        # sbin_score = self.spa_bin_score(spa_feat)
        # sbin_prob = F.sigmoid(sbin_score)

        # compute object classification probability
        icls_score = self.iRCNN_cls_score(iroi_pooled_feat)
        icls_prob = F.sigmoid(icls_score)
        # ibin_score = self.iRCNN_bin_score(iroi_pooled_feat)
        # ibin_prob = F.sigmoid(ibin_score)

        hcls_score = self.hRCNN_cls_score(hroi_pooled_feat)
        hcls_prob = F.sigmoid(hcls_score)
        # hbin_score = self.hRCNN_bin_score(hroi_pooled_feat)
        # hbin_prob = F.sigmoid(hbin_score)

        ocls_score = self.oRCNN_cls_score(oroi_pooled_feat)
        ocls_prob = F.sigmoid(ocls_score)
        # obin_score = self.oRCNN_bin_score(oroi_pooled_feat)
        # obin_prob = F.sigmoid(obin_score)

        cls_prob = (icls_prob + hcls_prob + ocls_prob) * scls_prob
        # bin_prob = (ibin_prob + hbin_prob + obin_prob) * sbin_prob

        spa_bin_feat = self.spa_bin_CNN(spa_maps[0])
        roi_cat_feat = torch.cat([iroi_pooled_feat, hroi_pooled_feat, oroi_pooled_feat], dim=1)
        roi_bin_feat = self.RCNN_bin_feat(roi_cat_feat)
        roi_spa_bin_feat = torch.cat([spa_bin_feat, roi_bin_feat], dim=1)
        bin_score = self.RCNN_bin_score(roi_spa_bin_feat)
        bin_prob = F.sigmoid(bin_score)


        RCNN_loss_cls = 0
        RCNN_loss_bin = 0

        if self.training:
            # classification loss
            scls_loss = F.binary_cross_entropy(scls_prob, hoi_classes.view(-1, hoi_classes.shape[2]), size_average=False)
            icls_loss = F.binary_cross_entropy(icls_prob, hoi_classes.view(-1, hoi_classes.shape[2]), size_average=False)
            hcls_loss = F.binary_cross_entropy(hcls_prob, hoi_classes.view(-1, hoi_classes.shape[2]), size_average=False)
            ocls_loss = F.binary_cross_entropy(ocls_prob, hoi_classes.view(-1, hoi_classes.shape[2]), size_average=False)

            RCNN_loss_cls = scls_loss + icls_loss + hcls_loss + ocls_loss

            # sbin_loss = F.binary_cross_entropy(sbin_prob, bin_classes.view(-1, bin_classes.shape[2]), size_average=False)
            # ibin_loss = F.binary_cross_entropy(ibin_prob, bin_classes.view(-1, bin_classes.shape[2]), size_average=False)
            # hbin_loss = F.binary_cross_entropy(hbin_prob, bin_classes.view(-1, bin_classes.shape[2]), size_average=False)
            # obin_loss = F.binary_cross_entropy(obin_prob, bin_classes.view(-1, bin_classes.shape[2]), size_average=False)
            # RCNN_loss_bin = sbin_loss + ibin_loss + hbin_loss + obin_loss

            RCNN_loss_bin = F.binary_cross_entropy(bin_prob, bin_classes.view(-1, bin_classes.shape[2]), size_average=False)

        cls_prob = cls_prob.view(batch_size, irois.size(1), -1)
        bin_prob = bin_prob.view(batch_size, irois.size(1), -1)

        # stub: bin_prob = Variable(torch.zeros(batch_size, irois.size(1), 2)).cuda()


        return cls_prob, bin_prob, RCNN_loss_cls, RCNN_loss_bin

    def _init_weights(self):
        def normal_init(m, mean, stddev, truncated=False):
            """
            weight initalizer: truncated normal and random normal.
            """
            # x is a parameter
            if truncated:
                m.weight.data.normal_().fmod_(2).mul_(stddev).add_(mean)  # not a perfect approximation
            else:
                m.weight.data.normal_(mean, stddev)
                m.bias.data.zero_()


        for layer in self.iRCNN_cls_score:
            if hasattr(layer, 'weight'):
                normal_init(layer, 0, 0.01, cfg.TRAIN.TRUNCATED)
        # normal_init(self.iRCNN_bin_score, 0, 0.01, cfg.TRAIN.TRUNCATED)

        for layer in self.hRCNN_cls_score:
            if hasattr(layer, 'weight'):
                normal_init(layer, 0, 0.01, cfg.TRAIN.TRUNCATED)
        # normal_init(self.hRCNN_bin_score, 0, 0.01, cfg.TRAIN.TRUNCATED)

        for layer in self.oRCNN_cls_score:
            if hasattr(layer, 'weight'):
                normal_init(layer, 0, 0.01, cfg.TRAIN.TRUNCATED)
        # normal_init(self.oRCNN_bin_score, 0, 0.01, cfg.TRAIN.TRUNCATED)

    def create_architecture(self):
        self._init_modules()
        self._init_weights()
