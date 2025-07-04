#!/bin/bash

module load mamba

rm -rf env
mamba env create -f env.yml -p ./env

source activate env/
conda env config vars set MUJOCO_GL=egl PYOPENGL_PLATFORM=egl

source deactivate && source activate env/