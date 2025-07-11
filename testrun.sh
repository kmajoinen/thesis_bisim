#!/bin/bash

#SBATCH --mem=70G
#SBATCH --gpus=1
#SBATCH --partition=gpu-debug
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=8
#SBATCH --job-name=bisim_base
#SBATCH --error=./outfiles/error_%A_.err
#SBATCH --output=./outfiles/bisim_%A_.out

mkdir -p outputs

module load mamba
source activate env/

DOMAIN="finger"
TASK="spin"
SEED=1
BETA=0.0
MAX_STEP=1100000

srun python3 train.py \
    --domain_name $DOMAIN \
    --task_name $TASK \
    --encoder_type pixel \
    --decoder_type identity \
    --action_repeat 2 \
    --work_dir ./log \
    --num_train_steps $MAX_STEP \
    --seed $SEED \
    --wandb-sync \
    --tr-beta $BETA \
    > outputs/test_bisim_outs-$SLURM_JOB_ID.txt