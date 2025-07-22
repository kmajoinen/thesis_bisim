#!/bin/bash

#SBATCH --mem=100G
#SBATCH --gpus=1
#SBATCH --partition=gpu-h100-80g,gpu-a100-80g,gpu-v100-32g
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=12
#SBATCH --job-name=bisim_tr_fi
#SBATCH --error=./outfiles/tr_error_%A_.err
#SBATCH --output=./outfiles/bisim_tr_%A_.out

mkdir -p outputs

module load mamba
source activate env/

DOMAIN="cheetah"
TASK="run"
SEED=1
ACTION_REPEAT=4
BETA=0.01
MAX_STEP=1100000

srun python3 train.py \
    --domain_name $DOMAIN \
    --task_name $TASK \
    --encoder_type pixel \
    --decoder_type identity \
    --action_repeat $ACTION_REPEAT \
    --work_dir ./log \
    --seed $SEED \
    --wandb-sync \
    --tr \
    --tr-beta $BETA \
    > outputs/bisim_outs_tr_$SLURM_JOB_ID.txt
