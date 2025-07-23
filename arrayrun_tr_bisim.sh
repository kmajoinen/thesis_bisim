#!/bin/bash

#SBATCH --mem=80G
#SBATCH --gpus=1
#SBATCH --partition=gpu-h100-80g,gpu-a100-80g,gpu-v100-32g
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=12
#SBATCH --job-name=bisim_tr_ch
#SBATCH --error=./errors/tr_error_%A_%a.err
#SBATCH --output=./outfiles/bisim_tr_%A_%a.out
#SBATCH --array=0-4

mkdir -p outputs

module load mamba
source activate env/

SEEDS=(
  1
  2
  3
  4
  5
)

DOMAIN="cheetah"
TASK="run"
SEED=1
ACTION_REPEAT=4
BETA=0.00001
MAX_STEP=1500000
SEED=$((SLURM_ARRAY_TASK_ID))

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
    --num_train_steps $MAX_STEP \
    > outputs/bisim_outs_tr_$SLURM_JOB_ID.txt