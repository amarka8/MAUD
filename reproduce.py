# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Finetuning the library models for question-answering on SQuAD (DistilBERT, Bert, XLM, XLNet)."""
"""Adapted from the HuggingFace SQuAD finetuning script for CUAD."""


import argparse
import glob
import logging
import os
import random
import timeit
import json

import numpy as np
import pathlib
import torch
from torch.utils.data import DataLoader, RandomSampler, DistributedSampler, SequentialSampler, SubsetRandomSampler
from tqdm import tqdm, trange

# import transformers
from transformers import (
    MODEL_FOR_QUESTION_ANSWERING_MAPPING,
    WEIGHTS_NAME,
    # AdamW,
    AutoConfig,
    AutoModelForQuestionAnswering,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
    squad_convert_examples_to_features,
)
# from utils import (
#     compute_predictions_logits,
#     squad_evaluate,
# )
from transformers.data.processors.squad import SquadResult, SquadV1Processor, SquadV2Processor
from transformers.trainer_utils import is_main_process


# try:
#     from torch.utils.tensorboard import SummaryWriter
# except ImportError:
#     from tensorboardX import SummaryWriter

logger = logging.getLogger(__name__)

MODEL_CONFIG_CLASSES = list(MODEL_FOR_QUESTION_ANSWERING_MAPPING.keys())
MODEL_TYPES = tuple(conf.model_type for conf in MODEL_CONFIG_CLASSES)


def set_seed(args):
    if args.seed is None:
        return
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)


# def to_list(tensor):
#     return tensor.detach().cpu().tolist()


def get_dataset_pos_mask(dataset):
    """
    Returns a list, pos_mask, where pos_mask[i] indicates is True if the ith example in the dataset is positive
    (i.e. it contains some text that should be highlighted) and False otherwise.
    """
    pos_mask = []
    for i in range(len(dataset)):
        ex = dataset[i]
        start_pos = ex[3]
        end_pos = ex[4]
        is_positive = end_pos > start_pos
        pos_mask.append(is_positive)
    return pos_mask


# def get_random_subset(dataset, keep_frac=1):
#     """
#     Takes a random subset of dataset, where a keep_frac fraction is kept.
#     """
#     keep_indices = [i for i in range(len(dataset)) if np.random.random() < keep_frac]
#     subset_dataset = torch.utils.data.Subset(dataset, keep_indices)
#     return subset_dataset


def get_balanced_dataset(dataset):
    """
    returns a new dataset, where positive and negative examples are approximately balanced
    """
    pos_mask = get_dataset_pos_mask(dataset)
    neg_mask = [~mask for mask in pos_mask]
    npos, nneg = np.sum(pos_mask), np.sum(neg_mask)

    neg_keep_frac = npos / nneg  # So that in expectation there will be npos negative examples (--> balanced), ex: ~ 300/700
    print("npos: " + npos)
    print("nneg: " + nneg)
    print("npos/nneg: " + neg_keep_frac)
    print("original mask: ")
    neg_keep_mask = [mask and np.random.random() < neg_keep_frac for mask in neg_mask]

    # keep all positive examples and subset of negative examples
    keep_mask = [pos_mask[i] or neg_keep_mask[i] for i in range(len(pos_mask))]
    keep_indices = [i for i in range(len(keep_mask)) if keep_mask[i]]

    subset_dataset = torch.utils.data.Subset(dataset, keep_indices)
    return subset_dataset

# def train(args, train_dataset, model, tokenizer):
#     """ Train the model """
#     if args.local_rank in [-1, 0]:
#         tb_writer = SummaryWriter()

#     args.train_batch_size = args.per_gpu_train_batch_size * max(1, args.n_gpu)
#     if args.keep_frac < 1:
#         train_dataset = get_random_subset(train_dataset, keep_frac=args.keep_frac)

#     train_sampler = RandomSampler(train_dataset) if args.local_rank == -1 else DistributedSampler(train_dataset)
#     train_dataloader = DataLoader(train_dataset, sampler=train_sampler, batch_size=args.train_batch_size)

#     if args.max_steps > 0:
#         t_total = args.max_steps
#         args.num_train_epochs = args.max_steps // (len(train_dataloader) // args.gradient_accumulation_steps) + 1
#     else:
#         t_total = len(train_dataloader) // args.gradient_accumulation_steps * args.num_train_epochs

#     # Prepare optimizer and schedule (linear warmup and decay)
#     no_decay = ["bias", "LayerNorm.weight"]
#     optimizer_grouped_parameters = [
#         {
#             "params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
#             "weight_decay": args.weight_decay,
#         },
#         {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], "weight_decay": 0.0},
#     ]
#     optimizer = AdamW(optimizer_grouped_parameters, lr=args.learning_rate, eps=args.adam_epsilon)
#     scheduler = get_linear_schedule_with_warmup(
#         optimizer, num_warmup_steps=args.warmup_steps, num_training_steps=t_total
#     )

#     # Check if saved optimizer or scheduler states exist
#     if os.path.isfile(os.path.join(args.model_name_or_path, "optimizer.pt")) and os.path.isfile(
#         os.path.join(args.model_name_or_path, "scheduler.pt")
#     ):
#         # Load in optimizer and scheduler states
#         optimizer.load_state_dict(torch.load(os.path.join(args.model_name_or_path, "optimizer.pt")))
#         scheduler.load_state_dict(torch.load(os.path.join(args.model_name_or_path, "scheduler.pt")))

#     if args.fp16:
#         try:
#             from apex import amp
#         except ImportError:
#             raise ImportError("Please install apex from https://www.github.com/nvidia/apex to use fp16 training.")

#         model, optimizer = amp.initialize(model, optimizer, opt_level=args.fp16_opt_level)

#     # multi-gpu training (should be after apex fp16 initialization)
#     if args.n_gpu > 1:
#         model = torch.nn.DataParallel(model)

#     # Distributed training (should be after apex fp16 initialization)
#     if args.local_rank != -1:
#         model = torch.nn.parallel.DistributedDataParallel(
#             model, device_ids=[args.local_rank], output_device=args.local_rank, find_unused_parameters=True
#         )

#     # Train!
#     logger.info("***** Running training *****")
#     logger.info("  Num examples = %d", len(train_dataset))
#     logger.info("  Num Epochs = %d", args.num_train_epochs)
#     logger.info("  Instantaneous batch size per GPU = %d", args.per_gpu_train_batch_size)
#     logger.info(
#         "  Total train batch size (w. parallel, distributed & accumulation) = %d",
#         args.train_batch_size
#         * args.gradient_accumulation_steps
#         * (torch.distributed.get_world_size() if args.local_rank != -1 else 1),
#     )
#     logger.info("  Gradient Accumulation steps = %d", args.gradient_accumulation_steps)
#     logger.info("  Total optimization steps = %d", t_total)

#     global_step = 1
#     epochs_trained = 0
#     steps_trained_in_current_epoch = 0
#     # Check if continuing training from a checkpoint
#     if os.path.exists(args.model_name_or_path):
#         try:
#             # set global_step to gobal_step of last saved checkpoint from model path
#             checkpoint_suffix = args.model_name_or_path.split("-")[-1].split("/")[0]
#             global_step = int(checkpoint_suffix)
#             epochs_trained = global_step // (len(train_dataloader) // args.gradient_accumulation_steps)
#             steps_trained_in_current_epoch = global_step % (len(train_dataloader) // args.gradient_accumulation_steps)

#             logger.info("  Continuing training from checkpoint, will skip to saved global_step")
#             logger.info("  Continuing training from epoch %d", epochs_trained)
#             logger.info("  Continuing training from global step %d", global_step)
#             logger.info("  Will skip the first %d steps in the first epoch", steps_trained_in_current_epoch)
#         except ValueError:
#             logger.info("  Starting fine-tuning.")

#     tr_loss, logging_loss = 0.0, 0.0
#     model.zero_grad()
#     train_iterator = trange(
#         epochs_trained, int(args.num_train_epochs), desc="Epoch", disable=args.local_rank not in [-1, 0]
#     )
#     # Added here for reproductibility
#     set_seed(args)

#     for _ in train_iterator:
#         epoch_iterator = tqdm(train_dataloader, desc="Iteration", disable=args.local_rank not in [-1, 0])
#         for step, batch in enumerate(epoch_iterator):

#             # Skip past any already trained steps if resuming training
#             if steps_trained_in_current_epoch > 0:
#                 steps_trained_in_current_epoch -= 1
#                 continue

#             model.train()
#             batch = tuple(t.to(args.device) for t in batch)

#             inputs = {
#                 "input_ids": batch[0],
#                 "attention_mask": batch[1],
#                 "token_type_ids": batch[2],
#                 "start_positions": batch[3],
#                 "end_positions": batch[4],
#             }

#             if args.model_type in ["xlm", "roberta", "distilbert", "camembert", "bart", "longformer"]:
#                 del inputs["token_type_ids"]

#             if args.model_type in ["xlnet", "xlm"]:
#                 raise NotImplementedError

#             outputs = model(**inputs)
#             # model outputs are always tuple in transformers (see doc)
#             loss = outputs[0]

#             if args.n_gpu > 1:
#                 loss = loss.mean()  # mean() to average on multi-gpu parallel (not distributed) training
#             if args.gradient_accumulation_steps > 1:
#                 loss = loss / args.gradient_accumulation_steps

#             if args.fp16:
#                 with amp.scale_loss(loss, optimizer) as scaled_loss:
#                     scaled_loss.backward()
#             else:
#                 loss.backward()

#             tr_loss += loss.item()
#             if (step + 1) % args.gradient_accumulation_steps == 0:
#                 if args.fp16:
#                     torch.nn.utils.clip_grad_norm_(amp.master_params(optimizer), args.max_grad_norm)
#                 else:
#                     torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)

#                 optimizer.step()
#                 scheduler.step()  # Update learning rate schedule
#                 model.zero_grad()
#                 global_step += 1

#                 # Log metrics
#                 if args.local_rank in [-1, 0] and args.logging_steps > 0 and global_step % args.logging_steps == 0:
#                     # Only evaluate when single GPU otherwise metrics may not average well
#                     if args.local_rank == -1 and args.evaluate_during_training:
#                         results = evaluate(args, model, tokenizer)
#                         for key, value in results.items():
#                             tb_writer.add_scalar("eval_{}".format(key), value, global_step)
#                     tb_writer.add_scalar("lr", scheduler.get_lr()[0], global_step)
#                     tb_writer.add_scalar("loss", (tr_loss - logging_loss) / args.logging_steps, global_step)
#                     logging_loss = tr_loss

#                 # Save model checkpoint
#                 # if args.local_rank in [-1, 0] and args.save_steps > 0 and global_step % args.save_steps == 0:
#                 #     output_dir = os.path.join(args.output_dir, "checkpoint-{}".format(global_step))
#                 #     # Take care of distributed/parallel training
#                 #     model_to_save = model.module if hasattr(model, "module") else model
#                 #     model_to_save.save_pretrained(output_dir)
#                 #     tokenizer.save_pretrained(output_dir)

#                 #     torch.save(args, os.path.join(output_dir, "training_args.bin"))
#                 #     logger.info("Saving model checkpoint to %s", output_dir)

#                 #     torch.save(optimizer.state_dict(), os.path.join(output_dir, "optimizer.pt"))
#                 #     torch.save(scheduler.state_dict(), os.path.join(output_dir, "scheduler.pt"))
#                 #     logger.info("Saving optimizer and scheduler states to %s", output_dir)

#             if args.max_steps > 0 and global_step > args.max_steps:
#                 epoch_iterator.close()
#                 break
#         if args.max_steps > 0 and global_step > args.max_steps:
#             train_iterator.close()
#             break

#     if args.local_rank in [-1, 0]:
#         tb_writer.close()

#     return global_step, tr_loss / global_step


# def evaluate(args, model, tokenizer, prefix=""):
#     dataset, examples, features = load_and_cache_examples(args, tokenizer, evaluate=True, output_examples=True)

#     with open(args.predict_file, "r") as f:
#         json_test_dict = json.load(f)

#     if not os.path.exists(args.output_dir) and args.local_rank in [-1, 0]:
#         os.makedirs(args.output_dir)

#     args.eval_batch_size = args.per_gpu_eval_batch_size * max(1, args.n_gpu)

#     # Note that DistributedSampler samples randomly
#     eval_sampler = SequentialSampler(dataset)
#     eval_dataloader = DataLoader(dataset, sampler=eval_sampler, batch_size=args.eval_batch_size)

#     # multi-gpu evaluate
#     if args.n_gpu > 1 and not isinstance(model, torch.nn.DataParallel):
#         model = torch.nn.DataParallel(model)

#     # Eval!
#     logger.info("***** Running evaluation {} *****".format(prefix))
#     logger.info("  Num examples = %d", len(dataset))
#     logger.info("  Batch size = %d", args.eval_batch_size)

#     all_results = []
#     start_time = timeit.default_timer()

#     for batch in tqdm(eval_dataloader, desc="Evaluating"):
#         model.eval()
#         batch = tuple(t.to(args.device) for t in batch)

#         with torch.no_grad():
#             inputs = {
#                 "input_ids": batch[0],
#                 "attention_mask": batch[1],
#                 "token_type_ids": batch[2],
#             }

#             if args.model_type in ["xlm", "roberta", "distilbert", "camembert", "bart", "longformer"]:
#                 del inputs["token_type_ids"]

#             feature_indices = batch[3]

#             # XLNet and XLM use more arguments for their predictions
#             if args.model_type in ["xlnet", "xlm"]:
#                 raise NotImplementedError
#             outputs = model(**inputs)

#         for i, feature_index in enumerate(feature_indices):
#             eval_feature = features[feature_index.item()]
#             unique_id = int(eval_feature.unique_id)

#             output = [to_list(output[i]) for output in outputs.to_tuple()]

#             # Some models (XLNet, XLM) use 5 arguments for their predictions, while the other "simpler"
#             # models only use two.
#             if len(output) >= 5:
#                 start_logits = output[0]
#                 start_top_index = output[1]
#                 end_logits = output[2]
#                 end_top_index = output[3]
#                 cls_logits = output[4]

#                 result = SquadResult(
#                     unique_id,
#                     start_logits,
#                     end_logits,
#                     start_top_index=start_top_index,
#                     end_top_index=end_top_index,
#                     cls_logits=cls_logits,
#                 )

#             else:
#                 start_logits, end_logits = output
#                 result = SquadResult(unique_id, start_logits, end_logits)

#             all_results.append(result)

#     evalTime = timeit.default_timer() - start_time
#     logger.info("  Evaluation done in total %f secs (%f sec per example)", evalTime, evalTime / len(dataset))

#     # Compute predictions
#     output_prediction_file = os.path.join(args.output_dir, "predictions_{}.json".format(prefix))
#     output_nbest_file = os.path.join(args.output_dir, "nbest_predictions_{}.json".format(prefix))

#     if args.version_2_with_negative:
#         output_null_log_odds_file = os.path.join(args.output_dir, "null_odds_{}.json".format(prefix))
#     else:
#         output_null_log_odds_file = None

#     # XLNet and XLM use a more complex post-processing procedure
#     if args.model_type in ["xlnet", "xlm"]:
#         raise NotImplementedError
#     else:
#         predictions = compute_predictions_logits(
#             json_test_dict,
#             examples,
#             features,
#             all_results,
#             args.n_best_size,
#             args.max_answer_length,
#             args.do_lower_case,
#             output_prediction_file,
#             output_nbest_file,
#             output_null_log_odds_file,
#             args.verbose_logging,
#             args.version_2_with_negative,
#             args.null_score_diff_threshold,
#             tokenizer,
#         )

#     # Compute the F1 and exact scores.
#     results = squad_evaluate(examples, predictions)
#     print(results)
#     return results


def load_and_cache_examples(args, tokenizer, evaluate=False, output_examples=False):
    # if args.local_rank not in [-1, 0] and not evaluate:
        # Make sure only the first process in distributed training process the dataset, and the others will use the cache
        # torch.distributed.barrier()

    # Load data features from cache or dataset file
    input_dir = args.data_dir if args.data_dir else "."
    cached_features_file = os.path.join(
        args.cache_dir,
        "cached_{}_{}_{}".format(
            "dev" if evaluate else "train",
            list(filter(None, args.model_name_or_path.split("/"))).pop(),
            str(args.max_seq_length),
        ),
    )
    subset_cached_features_file = os.path.join(
        args.cache_dir,
        "balanced_subset_cached_{}_{}_{}".format(
            "dev" if evaluate else "train",
            list(filter(None, args.model_name_or_path.split("/"))).pop(),
            str(args.max_seq_length),
        ),
    )
    print(subset_cached_features_file)
    args.cache_dir.mkdir(exist_ok=True, parents=True)

    # Init features and dataset from cache if it exists
    if os.path.exists(subset_cached_features_file) and not args.overwrite_cache:
        logger.info("Loading features from balanced cached file %s", subset_cached_features_file)
        dataset = torch.load(subset_cached_features_file)["dataset"]
        features, examples = None, None
    elif os.path.exists(cached_features_file) and not args.overwrite_cache:
        logger.info("Loading features from cached file %s", cached_features_file)
        features_and_dataset = torch.load(cached_features_file)
        if evaluate:
            features, dataset, examples = (
                features_and_dataset["features"],
                features_and_dataset["dataset"],
                features_and_dataset["examples"],
            )
        else:
            dataset = features_and_dataset["dataset"]
            dataset = get_balanced_dataset(dataset)
            if args.local_rank in [-1, 0]:
                logger.info("Saving balanced dataset into cached file %s", subset_cached_features_file)
                torch.save({"dataset": dataset}, subset_cached_features_file)
    else:
        logger.info("Creating features from dataset file at %s", input_dir)

        if not args.data_dir and ((evaluate and not args.predict_file) or (not evaluate and not args.train_file)):
            try:
                import tensorflow_datasets as tfds
                logging.warning('trying using tensor flow datasets')  # will not print anything
            except ImportError:
                logging.warning('trying to use tensor flow datasets but not installed')  # will not print anything
                # raise ImportError("If not data_dir is specified, tensorflow_datasets needs to be installed.")

            if args.version_2_with_negative:
                logger.warning("tensorflow_datasets does not handle version 2 of SQuAD.") # will not print anything

            tfds_examples = tfds.load("squad")
            examples = SquadV1Processor().get_examples_from_dataset(tfds_examples, evaluate=evaluate)
        else:
            logging.info('loading training files in load and cache')
            processor = SquadV2Processor() if args.version_2_with_negative else SquadV1Processor()
            if evaluate:
                examples = processor.get_dev_examples(args.data_dir, filename=args.predict_file)
            else:
                examples = processor.get_train_examples(args.data_dir, filename=args.train_file)

        features, dataset = squad_convert_examples_to_features(
            examples=examples,
            tokenizer=tokenizer,
            max_seq_length=args.max_seq_length,
            doc_stride=args.doc_stride,
            max_query_length=args.max_query_length,
            is_training=not evaluate,
            return_dataset="pt",
            threads=args.threads,
        )

        if evaluate:
            if args.local_rank in [-1, 0]:
                logger.info("Saving features into cached file %s", cached_features_file)
                torch.save({"features": features, "dataset": dataset, "examples": examples}, cached_features_file)
        else:
            dataset = get_balanced_dataset(dataset)
            if args.local_rank in [-1, 0]:
                logger.info("Saving balanced dataset into cached file %s", subset_cached_features_file)
                torch.save({"dataset": dataset}, subset_cached_features_file)

    if args.local_rank == 0 and not evaluate:
        # Make sure only the first process in distributed training process the dataset, and the others will use the cache
        torch.distributed.barrier()

    if output_examples:
        return dataset, examples, features
    return dataset


def main():
    parser = argparse.ArgumentParser()

    # Required parameters
    parser.add_argument(
        "--model_type",
        default=None,
        type=str,
        required=True,
        help="Model type selected in the list: " + ", ".join(MODEL_TYPES),
    )
    parser.add_argument(
        "--model_name_or_path",
        default=None,
        type=str,
        required=True,
        help="Path to pretrained model or model identifier from huggingface.co/models",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        type=str,
        required=True,
        help="The output directory where the model checkpoints and predictions will be written.",
    )

    # Other parameters
    parser.add_argument(
        "--data_dir",
        default=None,
        type=str,
        help="The input data dir. Should contain the .json files for the task."
        + "If no data dir or train/predict files are specified, will run with tensorflow_datasets.",
    )
    parser.add_argument(
        "--train_file",
        default=None,
        type=str,
        help="The input training file. If a data dir is specified, will look for the file there"
        + "If no data dir or train/predict files are specified, will run with tensorflow_datasets.",
    )
    parser.add_argument(
        "--predict_file",
        default=None,
        type=str,
        help="The input evaluation file. If a data dir is specified, will look for the file there"
        + "If no data dir or train/predict files are specified, will run with tensorflow_datasets.",
    )
    parser.add_argument(
        "--config_name", default="", type=str, help="Pretrained config name or path if not the same as model_name"
    )
    parser.add_argument(
        "--tokenizer_name",
        default="",
        type=str,
        help="Pretrained tokenizer name or path if not the same as model_name",
    )
    parser.add_argument(
        "--cache_dir",
        default="",
        type=pathlib.Path,
        help="Where do you want to store the pre-trained models downloaded from huggingface.co",
    )

    parser.add_argument(
        "--version_2_with_negative",
        action="store_true",
        help="If true, the SQuAD examples contain some that do not have an answer.",
    )
    parser.add_argument(
        "--null_score_diff_threshold",
        type=float,
        default=0.0,
        help="If null_score - best_non_null is greater than the threshold predict null.",
    )

    parser.add_argument(
        "--max_seq_length",
        default=384,
        type=int,
        help="The maximum total input sequence length after WordPiece tokenization. Sequences "
        "longer than this will be truncated, and sequences shorter than this will be padded.",
    )
    parser.add_argument(
        "--doc_stride",
        default=128,
        type=int,
        help="When splitting up a long document into chunks, how much stride to take between chunks.",
    )
    parser.add_argument(
        "--max_query_length",
        default=64,
        type=int,
        help="The maximum number of tokens for the question. Questions longer than this will "
        "be truncated to this length.",
    )
    parser.add_argument("--do_train", action="store_true", help="Whether to run training.")
    parser.add_argument("--do_eval", action="store_true", help="Whether to run eval on the dev set.")
    parser.add_argument(
        "--evaluate_during_training", action="store_true", help="Run evaluation during training at each logging step."
    )
    parser.add_argument(
        "--do_lower_case", action="store_true", help="Set this flag if you are using an uncased model."
    )

    parser.add_argument("--per_gpu_train_batch_size", default=8, type=int, help="Batch size per GPU/CPU for training.")
    parser.add_argument(
        "--per_gpu_eval_batch_size", default=8, type=int, help="Batch size per GPU/CPU for evaluation."
    )
    parser.add_argument("--learning_rate", default=5e-5, type=float, help="The initial learning rate for Adam.")
    parser.add_argument(
        "--gradient_accumulation_steps",
        type=int,
        default=1,
        help="Number of updates steps to accumulate before performing a backward/update pass.",
    )
    parser.add_argument("--weight_decay", default=0.0, type=float, help="Weight decay if we apply some.")
    parser.add_argument("--adam_epsilon", default=1e-8, type=float, help="Epsilon for Adam optimizer.")
    parser.add_argument("--max_grad_norm", default=1.0, type=float, help="Max gradient norm.")
    parser.add_argument(
        "--num_train_epochs", default=1.0, type=float, help="Total number of training epochs to perform."
    )
    parser.add_argument(
        "--max_steps",
        default=-1,
        type=int,
        help="If > 0: set total number of training steps to perform. Override num_train_epochs.",
    )
    parser.add_argument("--warmup_steps", default=0, type=int, help="Linear warmup over warmup_steps.")
    parser.add_argument(
        "--n_best_size",
        default=20,
        type=int,
        help="The total number of n-best predictions to generate in the nbest_predictions.json output file.",
    )
    parser.add_argument(
        "--max_answer_length",
        default=30,
        type=int,
        help="The maximum length of an answer that can be generated. This is needed because the start "
        "and end predictions are not conditioned on one another.",
    )
    parser.add_argument(
        "--verbose_logging",
        action="store_true",
        help="If true, all of the warnings related to data processing will be printed. "
        "A number of warnings are expected for a normal SQuAD evaluation.",
    )
    parser.add_argument(
        "--lang_id",
        default=0,
        type=int,
        help="language id of input for language-specific xlm models (see tokenization_xlm.PRETRAINED_INIT_CONFIGURATION)",
    )

    parser.add_argument("--logging_steps", type=int, default=500, help="Log every X updates steps.")
    parser.add_argument("--save_steps", type=int, default=500, help="Save checkpoint every X updates steps.")
    parser.add_argument(
        "--eval_all_checkpoints",
        action="store_true",
        help="Evaluate all checkpoints starting with the same prefix as model_name ending and ending with step number",
    )
    parser.add_argument("--no_cuda", action="store_true", help="Whether not to use CUDA when available")
    parser.add_argument(
        "--overwrite_output_dir", action="store_true", help="Overwrite the content of the output directory"
    )
    parser.add_argument(
        "--overwrite_cache", action="store_true", help="Overwrite the cached training and evaluation sets"
    )
    parser.add_argument("--seed", type=int, default=42, help="random seed for initialization")

    parser.add_argument("--local_rank", type=int, default=-1, help="local_rank for distributed training on gpus")
    # parser.add_argument(
    #     "--fp16",
    #     action="store_true",
    #     help="Whether to use 16-bit (mixed) precision (through NVIDIA apex) instead of 32-bit",
    # )
    # parser.add_argument(
    #     "--fp16_opt_level",
    #     type=str,
    #     default="O1",
    #     help="For fp16: Apex AMP optimization level selected in ['O0', 'O1', 'O2', and 'O3']."
    #     "See details at https://nvidia.github.io/apex/amp.html",
    # )
    # parser.add_argument("--server_ip", type=str, default="", help="Can be used for distant debugging.")
    # parser.add_argument("--server_port", type=str, default="", help="Can be used for distant debugging.")

    parser.add_argument("--threads", type=int, default=1, help="multiple threads for converting example to features")
    parser.add_argument("--keep_frac", type=float, default=1.0, help="The fraction of the balanced dataset to keep.")
    args = parser.parse_args()

    if args.doc_stride >= args.max_seq_length - args.max_query_length:
        logger.warning(
            "WARNING - You've set a doc stride which may be superior to the document length in some "
            "examples. This could result in errors when building features from the examples. Please reduce the doc "
            "stride or increase the maximum length to ensure the features are correctly built."
        )

    if (
        os.path.exists(args.output_dir)
        and os.listdir(args.output_dir)
        and args.do_train
        and not args.overwrite_output_dir
    ):
        raise ValueError(
            "Output directory ({}) already exists and is not empty. Use --overwrite_output_dir to overcome.".format(
                args.output_dir
            )
        )
    
    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO,
        filename = 'logfile.log',
        encoding='utf-8'
    )

     # Setup GPU training
    if torch.backends.mps.is_available():
        logger.info("gpu being used")
        mps_device = torch.device("mps")
        args.device = mps_device
        args.n_gpu = 1
        
    else:
        logger.info("cpu being used")
        device = torch.device("cpu")
        args.n_gpu = 0 
        args.device = device

    logger.warning(
        "Process rank: %s, device: %s, n_gpu: %s, distributed training: %s",
        args.local_rank,
        args.device,
        args.n_gpu,
        bool(args.local_rank != -1)
    )
    # Set the verbosity to info of the Transformers logger (on main process only):
    # if is_main_process(args.local_rank):
    #     transformers.utils.logging.set_verbosity_info()
    #     transformers.utils.logging.enable_default_handler()
    #     transformers.utils.logging.enable_explicit_format()
    # Set seed
    set_seed(args)

    # Load pretrained model and tokenizer
    # if args.local_rank not in [-1, 0]:
        # Make sure only the first process in distributed training will download model & vocab
        # torch.distributed.barrier()

    args.model_type = args.model_type.lower()
    config = AutoConfig.from_pretrained(
        args.config_name if args.config_name else args.model_name_or_path,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer_name if args.tokenizer_name else args.model_name_or_path,
        do_lower_case=args.do_lower_case,
        use_fast=False,  # SquadDataset is not compatible with Fast tokenizers which have a smarter overflow handeling
    )
    model = AutoModelForQuestionAnswering.from_pretrained(
        args.model_name_or_path,
        from_tf=bool(".ckpt" in args.model_name_or_path),
        config=config,
    )

    # if args.local_rank == 0:
        # Make sure only the first process in distributed training will download model & vocab
        # torch.distributed.barrier()

    model.to(args.device)

    logger.info("Training/evaluation parameters %s", args)

    # Before we do anything with models, we want to ensure that we get fp16 execution of torch.einsum if args.fp16 is set.
    # Otherwise it'll default to "promote" mode, and we'll get fp32 operations. Note that running `--fp16_opt_level="O2"` will
    # remove the need for this code, but it is still valid.
    # if args.fp16:
    #     try:
    #         import apex

    #         apex.amp.register_half_function(torch, "einsum")
    #     except ImportError:
    #         raise ImportError("Please install apex from https://www.github.com/nvidia/apex to use fp16 training.")

    # Training
    if args.do_train:
        # NOTE: balances dataset in load_and_cache_examples
        train_dataset = load_and_cache_examples(args, tokenizer, evaluate=False, output_examples=False)
        # global_step, tr_loss = train(args, train_dataset, model, tokenizer)
        # logger.info(" global_step = %s, average loss = %s", global_step, tr_loss)

    # Save the trained model and the tokenizer
    # if args.do_train and (args.local_rank == -1 or torch.distributed.get_rank() == 0):
    #     logger.info("Saving model checkpoint to %s", args.output_dir)
        # Save a trained model, configuration and tokenizer using `save_pretrained()`.
        # They can then be reloaded using `from_pretrained()`
        # Take care of distributed/parallel training
        # model_to_save = model.module if hasattr(model, "module") else model
        # model_to_save.save_pretrained(args.output_dir)
        # tokenizer.save_pretrained(args.output_dir)

        # Good practice: save your training arguments together with the trained model
        # torch.save(args, os.path.join(args.output_dir, "training_args.bin"))

        # Load a trained model and vocabulary that you have fine-tuned
        # model = AutoModelForQuestionAnswering.from_pretrained(args.output_dir)  # , force_download=True)

        # SquadDataset is not compatible with Fast tokenizers which have a smarter overflow handeling
        # So we use use_fast=False here for now until Fast-tokenizer-compatible-examples are out
        # tokenizer = AutoTokenizer.from_pretrained(args.output_dir, do_lower_case=args.do_lower_case, use_fast=False)
        # model.to(args.device)

    # Evaluation - we can ask to evaluate all the checkpoints (sub-directories) in a directory
    # results = {}
    # if args.do_eval and args.local_rank in [-1, 0]:
        # Evaluate
        # global_step = ""
        # result = evaluate(args, model, tokenizer, prefix=global_step)
        # result = dict((k + ("_{}".format(global_step) if global_step else ""), v) for k, v in result.items())
        # results.update(result)

    # return results


if __name__ == "__main__":
    main()