cache_type=split
eval_mode=valid

# for run_num in 1 2 3; do
#   for epoch_num in 4 6 8; do
    # for lr in 1e-5 1e-4 3e-5; do  # TRAIN ALL
for run_num in 1; do
  for epoch_num in 4; do
    for lr in 1e-5; do  # TRAIN ALL
      output_dir=./train_models/first_run_valid_${cache_type}/n_epoch_${epoch_num}/${run_num}/roberta-base-maud-lr-$lr
      # Initialize conda in your shell (adjust path if necessary)
      eval "$(conda shell.bash hook)"
      # Activate the conda environment
      conda activate faiss_1.8.0_new_env
      python3 reproduce.py \
              --output_dir $output_dir \
              --model_type roberta \
              --model_name_or_path roberta-base \
              --data_dir /Users/amarkanaka/repos/MAUD/MAUD-test-train-dev \
              --train_file /Users/amarkanaka/repos/MAUD/MAUD-test-train-dev/maud_squad_train.json \
              --predict_file /Users/amarkanaka/repos/MAUD/MAUD-test-train-dev/maud_squad_dev.json \
              --cache_dir ./_cached_features/${eval_mode}/maud_${cache_type} \
              --version_2_with_negative \
              --learning_rate $lr \
              --num_train_epochs ${epoch_num} \
              --per_gpu_eval_batch_size=16  \
              --per_gpu_train_batch_size=40 \
              --max_seq_length 512 \
              --max_answer_length 512 \
              --doc_stride 256 \
              --save_steps 1000 \
              --overwrite_output_dir \
              --threads 4 \
              --do_train \
              --do_eval \
              --n_best_size 100 

      echo
      echo $output_dir
    #   python evaluate.py -E test $output_dir
    done
done
done