import cv2
import torch
import numpy as np
import torchvision.transforms as transforms

from models.resnet_yolo import resnet50


VOC_CLASSES = (    # always index 0
    "aeroplane", "bicycle", "bird", "boat",
    "bottle", "bus", "car", "cat", "chair",
    "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant",
    "sheep", "sofa", "train", "tvmonitor"
)

# 数据集20个类别的所对应的20种颜色
Color = [
    [0, 0, 0], [128, 0, 0], [0, 128, 0],
    [128, 128, 0], [0, 0, 128], [128, 0, 128],
    [0, 128, 128], [128, 128, 128], [64, 0, 0],
    [192, 0, 0], [64, 128, 0], [192, 128, 0],
    [64, 0, 128], [192, 0, 128], [64, 128, 128],
    [192, 128, 128], [0, 64, 0], [128, 64, 0],
    [0, 192, 0], [128, 192, 0], [0, 64, 128]
]

# 对于网络输出预测改为在图片上画出框及score
def decoder(pred):
    grid_num = 14
    boxes = []
    cls_indexs = []
    probs = []
    cell_size = 1. / grid_num
    pred = pred.data  # torch.Size([1, 14, 14, 30])
    pred = pred.squeeze(0)  # torch.Size([14, 14, 30])
    # [中心坐标,长宽,置信度,中心坐标,长宽,置信度, 20个类别] x 7x7
    contain1 = pred[:, :, 4].unsqueeze(2)
    contain2 = pred[:, :, 9].unsqueeze(2)
    contain = torch.cat((contain1, contain2), 2) # torch.Size([14, 14, 2])

    mask1 = contain > 0.1 # 大于阈值, torch.Size([14, 14, 2]) content: tensor([False, False])
    mask2 = (contain == contain.max()) # we always select the best contain_prob whatever it > 0.9
    mask = (mask1 + mask2).gt(0)

    # 每个cell只选最大概率的那个预测框
    boxes = []
    cls_indexs = []
    probs = []
    for i in range(grid_num):
        for j in range(grid_num):
            for b in range(2):
                if mask[i, j, b] == 1:
                    box = pred[i, j, b*5:b*5+4]
                    contain_prob = torch.FloatTensor([pred[i, j, b*5+4]])
                    xy = torch.FloatTensor([j, i]) * cell_size # cell左上角 up left of cell
                    box[:2] = box[:2] * cell_size + xy # return cxcy relative to image
                    box_xy = torch.FloatTensor(box.size()) # 转换成xy形式 convert[cx, cy, w, h] to [x1, y1, x2, y2]
                    box_xy[:2] = box[:2] - 0.5 * box[2:]
                    box_xy[2:] = box[:2] + 0.5 * box[2:]
                    max_prob, cls_index = torch.max(pred[i, j, 10:], 0)
                    if float((contain_prob * max_prob)[0]) > 0.1:
                        boxes.append(box_xy.view(1, 4))
                        cls_indexs.append(cls_index.item())
                        probs.append(contain_prob * max_prob)
    if len(boxes) == 0:
        boxes = torch.zeros((1, 4))
        probs = torch.zeros(1)
        cls_indexs = torch.zeros(1)
    else:
        boxes = torch.cat(boxes, 0) # (n,4)
        probs = torch.cat(probs, 0) # (n,)
        cls_indexs = torch.IntTensor(cls_indexs) # (n,)
    keep = nms(boxes, probs)

    a = boxes[keep]
    b = cls_indexs[keep]
    c = probs[keep]
    return a, b, c


def nms(bboxes, scores, threshold=0.5):
    x1 = bboxes[:, 0]
    y1 = bboxes[:, 1]
    x2 = bboxes[:, 2]
    y2 = bboxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    _, order = scores.sort(0, descending=True) # 降序排列score
    keep = []
    while order.numel() > 0: # torch.numel()返回张量元素个数
        if order.numel() == 1: # 保留框只剩一个
            i = order
            keep.append(i)
            break
        i = order[0] # 保留scores最大的那个框box[i]
        keep.append(i)

        # 计算box[i]与其余各框box[order[1:]]的IOU
        xx1 = torch.max(x1[i], x1[order[1:]])
        yy1 = torch.max(y1[i], y1[order[1:]])
        xx2 = torch.min(x2[i], x2[order[1:]])
        yy2 = torch.min(y2[i], y2[order[1:]])
        inter = torch.clamp(xx2 - xx1 , min=0) * torch.clamp(yy2 - yy1 , min=0)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        ids = (ovr <= threshold).nonzero(as_tuple=False).squeeze() # 注意此时idx为[N - 1,], 而order为[N, ]
        if ids.numel() == 0:
            break
        order = order[ids + 1] # 修补索引之间的差值
    return torch.LongTensor(keep)


# start predict one image
def predict(model, image):
    result = []
    h, w, _ = image.shape
    img = cv2.resize(image, (448, 448))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    mean = (123, 117, 104) # RGB
    img = img - np.array(mean, dtype=np.float32)

    transform = transforms.Compose([transforms.ToTensor(), ])
    img = transform(img) # torch.Size([3, 448, 448])
    img = img[None, :, :, :] # img: torch.Size([1, 3, 448, 448])

    pred = model(img) # 1x14x14x30
    pred = pred.cpu()
    boxes, cls_indexs, probs = decoder(pred)

    for i, box in enumerate(boxes):
        x1 = int(box[0] * w)
        x2 = int(box[2] * w)
        y1 = int(box[1] * h)
        y2 = int(box[3] * h)
        cls_index = cls_indexs[i]
        cls_index = int(cls_index) # convert LongTensor to int
        prob = probs[i]
        prob = float(prob)
        result.append([(x1, y1), (x2, y2), VOC_CLASSES[cls_index], image_name, prob])
    return result


if __name__ == "__main__":
    model = resnet50()
    print("load model...")
    model.load_state_dict(torch.load("./yolo.pth", map_location=torch.device('cpu')))
    model.eval()
    image_name = "imgs/demo.jpg"
    image = cv2.imread(image_name)
    print("predicting...") 
    result = predict(model, image)

    for left_up, right_bottom, class_name, _, prob in result:
        color = Color[VOC_CLASSES.index(class_name)]
        cv2.rectangle(image, left_up, right_bottom, color, 2)
        label = class_name+str(round(prob, 2))
        text_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        p1 = (left_up[0], left_up[1] - text_size[1])
        cv2.rectangle(image, (p1[0] - 2//2, p1[1] - 2 - baseline), (p1[0] + text_size[0], p1[1] + text_size[1]), color, -1)
        cv2.putText(image, label, (p1[0], p1[1] + baseline), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, 8)

    cv2.imwrite("imgs/demo_result.jpg", image)