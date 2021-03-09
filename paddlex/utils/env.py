# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
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

import sys
import glob
import os
import os.path as osp
import numpy as np
import multiprocessing as mp
import paddle

from . import logging


def get_environ_info():
    """collect environment information"""

    env_info = dict()
    # TODO is_compiled_with_cuda() has not been moved
    compiled_with_cuda = paddle.is_compiled_with_cuda()
    if compiled_with_cuda:
        if 'gpu' in paddle.get_device():
            gpu_nums = paddle.distributed.ParallelEnv().nranks
        else:
            gpu_nums = 0
        if gpu_nums == 0:
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
    place = 'gpu' if compiled_with_cuda and gpu_nums else 'cpu'
    env_info['place'] = place
    env_info['num'] = int(os.environ.get('CPU_NUM', 1))
    if place == 'gpu':
        env_info['num'] = gpu_nums

    return env_info


def get_num_workers(num_workers):
    if num_workers == 'auto':
        num_workers = mp.cpu_count() // 2 if mp.cpu_count() // 2 < 8 else 8
    return num_workers