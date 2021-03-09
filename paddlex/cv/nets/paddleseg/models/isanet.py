# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from paddlex.cv.nets.paddleseg.models import layers
from paddlex.cv.nets.paddleseg.cvlibs import manager
from paddlex.cv.nets.paddleseg.utils import utils


@manager.MODELS.add_component
class ISANet(nn.Layer):
    """Interlaced Sparse Self-Attention for Semantic Segmentation.

    The original article refers to Lang Huang, et al. "Interlaced Sparse Self-Attention for Semantic Segmentation"
    (https://arxiv.org/abs/1907.12273).

    Args:
        num_classes (int): The unique number of target classes.
        backbone (Paddle.nn.Layer): A backbone network.
        backbone_indices (tuple): The values in the tuple indicate the indices of output of backbone.
        isa_channels (int): The channels of ISA Module.
        down_factor (tuple): Divide the height and width dimension to (Ph, PW) groups.
        enable_auxiliary_loss (bool, optional): A bool value indicates whether adding auxiliary loss. Default: True.
        align_corners (bool): An argument of F.interpolate. It should be set to False when the output size of feature
            is even, e.g. 1024x512, otherwise it is True, e.g. 769x769.  Default: False.
        pretrained (str, optional): The path or url of pretrained model. Default: None.

    """

    def __init__(self,
                 num_classes,
                 backbone,
                 backbone_indices=(2, 3),
                 isa_channels=256,
                 down_factor=(8, 8),
                 enable_auxiliary_loss=True,
                 align_corners=False,
                 pretrained=None):
        super().__init__()

        self.backbone = backbone
        self.backbone_indices = backbone_indices
        in_channels = [
            self.backbone.feat_channels[i] for i in backbone_indices
        ]
        self.head = ISAHead(num_classes, in_channels, isa_channels,
                            down_factor, enable_auxiliary_loss)
        self.align_corners = align_corners
        self.pretrained = pretrained
        self.init_weight()

    def forward(self, x):
        feats = self.backbone(x)
        feats = [feats[i] for i in self.backbone_indices]
        logit_list = self.head(feats)
        logit_list = [
            F.interpolate(
                logit,
                x.shape[2:],
                mode='bilinear',
                align_corners=self.align_corners,
                align_mode=1) for logit in logit_list
        ]

        return logit_list

    def init_weight(self):
        if self.pretrained is not None:
            utils.load_entire_model(self, self.pretrained)


class ISAHead(nn.Layer):
    """
    The ISAHead.

    Args:
        num_classes (int): The unique number of target classes.
        in_channels (tuple): The number of input channels.
        isa_channels (int): The channels of ISA Module.
        down_factor (tuple): Divide the height and width dimension to (Ph, PW) groups.
        enable_auxiliary_loss (bool, optional): A bool value indicates whether adding auxiliary loss. Default: True.
    """

    def __init__(self, num_classes, in_channels, isa_channels, down_factor,
                 enable_auxiliary_loss):
        super(ISAHead, self).__init__()
        self.in_channels = in_channels[-1]
        inter_channels = self.in_channels // 4
        self.down_factor = down_factor
        self.enable_auxiliary_loss = enable_auxiliary_loss
        self.in_conv = layers.ConvBNReLU(
            self.in_channels, inter_channels, 3, bias_attr=False)
        self.global_relation = SelfAttentionBlock(inter_channels, isa_channels)
        self.local_relation = SelfAttentionBlock(inter_channels, isa_channels)
        self.out_conv = layers.ConvBNReLU(
            inter_channels * 2, inter_channels, 1, bias_attr=False)
        self.cls = nn.Sequential(
            nn.Dropout2D(p=0.1), nn.Conv2D(inter_channels, num_classes, 1))
        self.aux = nn.Sequential(
            layers.ConvBNReLU(
                in_channels=1024,
                out_channels=256,
                kernel_size=3,
                bias_attr=False),
            nn.Dropout2D(p=0.1),
            nn.Conv2D(256, num_classes, 1))

    def forward(self, feat_list):
        C3, C4 = feat_list
        x = self.in_conv(C4)
        n, c, h, w = x.shape
        P_h, P_w = self.down_factor
        Q_h, Q_w = math.ceil(h / P_h), math.ceil(w / P_w)
        pad_h, pad_w = Q_h * P_h - h, Q_w * P_w - w
        if pad_h > 0 or pad_w > 0:
            padding = [
                pad_w // 2, pad_w - pad_w // 2, pad_h // 2, pad_h - pad_h // 2
            ]
            feat = F.pad(x, padding)
        else:
            feat = x

        feat = feat.reshape([n, c, Q_h, P_h, Q_w, P_w])
        feat = feat.transpose([0, 3, 5, 1, 2, 4]).reshape([-1, c, Q_h, Q_w])
        feat = self.global_relation(feat)

        feat = feat.reshape([n, P_h, P_w, c, Q_h, Q_w])
        feat = feat.transpose([0, 4, 5, 3, 1, 2]).reshape([-1, c, P_h, P_w])
        feat = self.local_relation(feat)

        feat = feat.reshape([n, Q_h, Q_w, c, P_h, P_w])
        feat = feat.transpose([0, 3, 1, 4, 2, 5]).reshape(
            [n, c, P_h * Q_h, P_w * Q_w])
        if pad_h > 0 or pad_w > 0:
            feat = feat[:, :, pad_h // 2:pad_h // 2 + h, pad_w // 2:pad_w // 2
                        + w]

        feat = self.out_conv(paddle.concat([feat, x], axis=1))
        output = self.cls(feat)

        if self.enable_auxiliary_loss:
            auxout = self.aux(C3)
            return [output, auxout]
        else:
            return [output]


class SelfAttentionBlock(layers.AttentionBlock):
    """General self-attention block/non-local block.

       Args:
            in_channels (int): Input channels of key/query feature.
            channels (int): Output channels of key/query transform.
    """

    def __init__(self, in_channels, channels):
        super(SelfAttentionBlock, self).__init__(
            key_in_channels=in_channels,
            query_in_channels=in_channels,
            channels=channels,
            out_channels=in_channels,
            share_key_query=False,
            query_downsample=None,
            key_downsample=None,
            key_query_num_convs=2,
            key_query_norm=True,
            value_out_num_convs=1,
            value_out_norm=False,
            matmul_norm=True,
            with_out=False)

        self.output_project = self.build_project(
            in_channels, in_channels, num_convs=1, use_conv_module=True)

    def forward(self, x):
        context = super(SelfAttentionBlock, self).forward(x, x)
        return self.output_project(context)