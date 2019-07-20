# Machine Reading Comprehension
Machine reading comprehension (MRC), a task which asks machine to read a given context then answer questions based on its understanding, is considered one of the key problems in artificial intelligence and has significant interest from both academic and industry. Over the past few years, great progress has been made in this field, thanks to various end-to-end trained neural models and high quality datasets with large amount of examples proposed.
<p align="center"><img src="/docs/squad.example.png" width=800></p>
<p align="center"><i>Figure 1: MRC example from SQuAD 2.0 dev set</i></p>

## Setting
* Python 3.6.7
* Tensorflow 1.13.1
* NumPy 1.13.3
* SentencePiece 0.1.82

## DataSet
* [SQuAD](https://rajpurkar.github.io/SQuAD-explorer/) is a reading comprehension dataset, consisting of questions posed by crowd-workers on a set of Wikipedia articles, where the answer to every question is a segment of text, or span, from the corresponding reading passage, or the question might be unanswerable.


## Usage
* Run train
```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 python run_squad.py \
    --spiece_model_file=model/cased_L-24_H-1024_A-16/spiece.model \
    --model_config_path=model/cased_L-24_H-1024_A-16/xlnet_config.json \
    --init_checkpoint=model/cased_L-24_H-1024_A-16/xlnet_model.ckpt \
    --task_name=v2.0 \
    --random_seed=100 \
    --predict_tag=xxxxx \
    --data_dir=data/squad/v2.0 \
    --output_dir=output/squad/v2.0/data \
    --model_dir=output/squad/v2.0/checkpoint \
    --export_dir=output/squad/v2.0/export \
    --max_seq_length=512 \
    --train_batch_size=12 \
    --predict_batch_size=8 \
    --num_hosts=1 \
    --num_core_per_host=4 \
    --learning_rate=3e-5 \
    --train_steps=8000 \
    --warmup_steps=1000 \
    --save_steps=1000 \
    --do_train=true \
    --do_predict=false \
    --do_export=false \
    --overwrite_data=false
```
* Run predict
```bash
CUDA_VISIBLE_DEVICES=0 python run_squad.py \
    --spiece_model_file=model/cased_L-24_H-1024_A-16/spiece.model \
    --model_config_path=model/cased_L-24_H-1024_A-16/xlnet_config.json \
    --init_checkpoint=model/cased_L-24_H-1024_A-16/xlnet_model.ckpt \
    --task_name=v2.0 \
    --random_seed=100 \
    --predict_tag=xxxxx \
    --data_dir=data/squad/v2.0 \
    --output_dir=output/squad/v2.0/data \
    --model_dir=output/squad/v2.0/checkpoint \
    --export_dir=output/squad/v2.0/export \
    --max_seq_length=512 \
    --train_batch_size=48 \
    --predict_batch_size=32 \
    --num_hosts=1 \
    --num_core_per_host=1 \
    --learning_rate=3e-5 \
    --train_steps=8000 \
    --warmup_steps=1000 \
    --save_steps=1000 \
    --do_train=false \
    --do_predict=true \
    --do_export=false \
    --overwrite_data=false
```
* Visualize summary
```bash
tensorboard --logdir=output/squad/v2.0
```
* Setup service
```bash
docker run -p 8500:8500 \
  -v output/squad/v2.0/export/xxxxx:models/squad \
  -e MODEL_NAME=squad \
  -t tensorflow/serving
```
