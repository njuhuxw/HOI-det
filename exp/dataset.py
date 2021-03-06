import numpy as np

import torch
from torch.utils.data import Dataset


def bbox_trans(human_box_roi, object_box_roi, size=64):
    human_box = human_box_roi.copy()
    object_box = object_box_roi.copy()

    union_box = [min(human_box[0], object_box[0]), min(human_box[1], object_box[1]),
                          max(human_box[2], object_box[2]), max(human_box[3], object_box[3])]

    height = union_box[3] - union_box[1] + 1
    width = union_box[2] - union_box[0] + 1

    if height > width:
        ratio = 'height'
    else:
        ratio = 'width'

    # shift the top-left corner to (0,0)
    human_box[0] -= union_box[0]
    human_box[2] -= union_box[0]
    human_box[1] -= union_box[1]
    human_box[3] -= union_box[1]
    object_box[0] -= union_box[0]
    object_box[2] -= union_box[0]
    object_box[1] -= union_box[1]
    object_box[3] -= union_box[1]

    if ratio == 'height':  # height is larger than width

        human_box[0] = 0 + size * human_box[0] / height
        human_box[1] = 0 + size * human_box[1] / height
        human_box[2] = (size * width / height - 1) - size * (width - 1 - human_box[2]) / height
        human_box[3] = (size - 1) - size * (height - 1 - human_box[3]) / height

        object_box[0] = 0 + size * object_box[0] / height
        object_box[1] = 0 + size * object_box[1] / height
        object_box[2] = (size * width / height - 1) - size * (width - 1 - object_box[2]) / height
        object_box[3] = (size - 1) - size * (height - 1 - object_box[3]) / height

        # Need to shift horizontally
        union_box = [min(human_box[0], object_box[0]), min(human_box[1], object_box[1]),
                              max(human_box[2], object_box[2]), max(human_box[3], object_box[3])]
        if human_box[3] > object_box[3]:
            human_box[3] = size - 1
        else:
            object_box[3] = size - 1

        shift = size / 2 - (union_box[2] + 1) / 2
        human_box += [shift, 0, shift, 0]
        object_box += [shift, 0, shift, 0]

    else:  # width is larger than height

        human_box[0] = 0 + size * human_box[0] / width
        human_box[1] = 0 + size * human_box[1] / width
        human_box[2] = (size - 1) - size * (width - 1 - human_box[2]) / width
        human_box[3] = (size * height / width - 1) - size * (height - 1 - human_box[3]) / width

        object_box[0] = 0 + size * object_box[0] / width
        object_box[1] = 0 + size * object_box[1] / width
        object_box[2] = (size - 1) - size * (width - 1 - object_box[2]) / width
        object_box[3] = (size * height / width - 1) - size * (height - 1 - object_box[3]) / width

        # Need to shift vertically
        union_box = [min(human_box[0], object_box[0]), min(human_box[1], object_box[1]),
                              max(human_box[2], object_box[2]), max(human_box[3], object_box[3])]

        if human_box[2] > object_box[2]:
            human_box[2] = size - 1
        else:
            object_box[2] = size - 1

        shift = size / 2 - (union_box[3] + 1) / 2

        human_box = human_box + [0, shift, 0, shift]
        object_box = object_box + [0, shift, 0, shift]

    return np.round(human_box), np.round(object_box)


def gen_spatial_map(human_box, object_box, obj_cls, num_obj_cls):
    hbox, obox = bbox_trans(human_box, object_box)
    spa_map = np.zeros((2, 64, 64), dtype='float32')
    spa_map[0, int(hbox[1]):int(hbox[3]) + 1, int(hbox[0]):int(hbox[2]) + 1] = 1
    spa_map[1, int(obox[1]):int(obox[3]) + 1, int(obox[0]):int(obox[2]) + 1] = 1
    return spa_map


def gen_pose_feat(skeleton, obj_box):

    def is_inside_box(pt, box):
        xmin, ymin, xmax, ymax = box
        pt_x, pt_y = pt
        return xmin <= pt_x <= xmax and ymin <= pt_y <= ymax

    pose_feat = np.zeros(17, dtype='float32')
    if skeleton is not None and isinstance(skeleton, list) and len(skeleton) == 51:
        for i in range(17):
            pose_x = skeleton[i*3+0]
            pose_y = skeleton[i*3+1]
            if is_inside_box((pose_x, pose_y), obj_box):
                pose_feat[i] = 1
    return pose_feat


class HICODatasetSpa(Dataset):

    def __init__(self, hoi_db):

        self.hboxes = hoi_db['hboxes']
        self.oboxes = hoi_db['oboxes']
        self.obj_classes = hoi_db['obj_classes']
        self.hoi_classes = torch.from_numpy(hoi_db['hoi_classes']).float()
        self.bin_classes = torch.from_numpy(hoi_db['bin_classes']).long()
        self.spa_feats = torch.from_numpy(hoi_db['spa_feats']).float()
        self.obj2vec = torch.from_numpy(hoi_db['obj2vec']).float()
        self.skeletons = hoi_db['skeletons']
        self.num_obj_class = 80

    def __len__(self):
        return len(self.hboxes)

    def __getitem__(self, item):
        spa_map = torch.from_numpy(gen_spatial_map(self.hboxes[item],
                                                   self.oboxes[item],
                                                   self.obj_classes[item], 80))
        obj_class_ind = self.obj_classes[item]
        obj_class_vec = torch.zeros((self.num_obj_class))
        obj_class_vec[obj_class_ind] = 1
        pose_feat = torch.from_numpy(gen_pose_feat(self.skeletons[item], self.oboxes[item]))
        return spa_map, \
               self.obj2vec[self.obj_classes[item].item()], \
               self.hoi_classes[item], \
               self.bin_classes[item], \
               obj_class_vec, \
               pose_feat
