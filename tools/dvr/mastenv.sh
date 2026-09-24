#!/bin/bash
# MASt3R for cpr_match.py, in its own venv next to gsenv (win4090 WSL). Checkpoint from Hugging Face.
set -e
cd ~
[ -d mast3r ] || git clone --recursive https://github.com/naver/mast3r
[ -d mastenv ] || uv venv ~/mastenv --python 3.11
. ~/mastenv/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
uv pip install -r mast3r/requirements.txt -r mast3r/dust3r/requirements.txt opencv-python-headless scipy huggingface_hub
cd ~/mast3r && python -c "
import sys; sys.path.insert(0,'.')
from mast3r.model import AsymmetricMASt3R
m = AsymmetricMASt3R.from_pretrained('naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric')
import torch; print('MAST3R-OK', torch.cuda.is_available())"
