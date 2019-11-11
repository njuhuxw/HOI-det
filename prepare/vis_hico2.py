import pickle
import numpy as np
from utils.show_box import show_boxes

body_parts = ["head",
              "left_hand",
              "right_hand",
              "hip",
              "left_leg",
              "right_leg"]


key_points = ["nose",
              "left_eye", "right_eye",
              "left_ear", "right_ear",
              "left_shoulder", "right_shoulder",
              "left_elbow", "right_elbow",
              "left_wrist", "right_wrist",
              "left_hip", "right_hip",
              "left_knee", "right_knee",
              "left_ankle", "right_ankle"]


def est_hand(wrist, elbow):
    return wrist - 0.5 * (wrist - elbow)


def get_body_part_kps(part, all_kps):
    all_part_kps = {
        'left_leg':  ['left_ankle'],
        'right_leg': ['right_ankle'],
        'left_hand':    ['left_hand', 'left_wrist', 'left_elbow'],
        'right_hand':   ['right_hand', 'right_wrist', 'right_elbow'],
        'hip':  ['left_hip', 'right_hip', 'left_knee', 'right_knee'],
        'head': ['nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear'],
    }

    kp2ind = dict(zip(key_points, range(len(key_points))))
    part_kps = np.zeros((len(all_part_kps[part]), 3))
    for i, kp_name in enumerate(all_part_kps[part]):
        if kp_name == 'left_hand':
            left_wrist = all_kps[kp2ind['left_wrist']]
            left_elbow = all_kps[kp2ind['left_elbow']]
            kp = est_hand(left_wrist, left_elbow)
        elif kp_name == 'right_hand':
            right_wrist = all_kps[kp2ind['right_wrist']]
            right_elbow = all_kps[kp2ind['right_elbow']]
            kp = est_hand(right_wrist, right_elbow)
        else:
            kp = all_kps[kp2ind[kp_name]]
        part_kps[i] = kp
    return part_kps


def get_body_part_alpha(part):
    all_body_part_alpha = {
        'head': 0.1,
        'left_hand': 0.1,
        'right_hand': 0.1,
        'hip': 0.1,
        'left_leg': 0.2,
        'right_leg': 0.2
    }
    return all_body_part_alpha[part]


def gen_body_part_box(all_kps, human_wh, part, kp_thr=0.01, area_thr=0):
    part_kps = get_body_part_kps(part, all_kps)
    xmin = 9999
    ymin = 9999
    xmax = 0
    ymax = 0
    for i in range(len(part_kps)):
        conf = part_kps[i, 2]
        if conf < kp_thr:
            return None

        xmin = min(xmin, part_kps[i, 0])
        ymin = min(ymin, part_kps[i, 1])
        xmax = max(xmax, part_kps[i, 0])
        ymax = max(ymax, part_kps[i, 1])

    if (ymax - ymin + 1) * (xmax - xmin + 1) < area_thr:
        return None
    return [xmin - get_body_part_alpha(part) * human_wh[0],
            ymin - get_body_part_alpha(part) * human_wh[1],
            xmax + get_body_part_alpha(part) * human_wh[0],
            ymax + get_body_part_alpha(part) * human_wh[1]]


anno_path = '../data/hico/train_GT_HICO_with_pose.pkl'
with open(anno_path) as f:
    anno_db = pickle.load(f)

img_path_template = '../data/hico/images/train2015/HICO_train2015_%s.jpg'
for ins_anno in anno_db:
    img_id = ins_anno[0]
    raw_kps = ins_anno[5]
    human_box = ins_anno[2]
    human_wh = [human_box[2] - human_box[0],
                human_box[3] - human_box[1]]
    img_path = img_path_template % (str(img_id).zfill(8))
    if raw_kps is None or len(raw_kps) != 51:
        continue

    all_kps = np.reshape(raw_kps, (len(key_points), 3))
    body_part_boxes = []
    body_part_names = []
    for body_part in body_parts:
        body_part_box = gen_body_part_box(all_kps, human_wh, body_part)
        if body_part_box is not None:
            body_part_boxes.append(body_part_box)
            body_part_names.append(body_part)


    show_boxes(img_path, body_part_boxes, body_part_names)

