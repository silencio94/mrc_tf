from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
sys.path.append('xlnet') # walkaround due to submodule absolute import...

import collections
import os
import os.path
import json
import pickle
import time

import tensorflow as tf
import numpy as np
import sentencepiece as sp

from xlnet import xlnet
import function_builder
import prepro_utils
import model_utils

MAX_FLOAT = 1e30
MIN_FLOAT = -1e30

flags = tf.flags
FLAGS = flags.FLAGS

flags.DEFINE_string("data_dir", None, "Data directory where raw data located.")
flags.DEFINE_string("output_dir", None, "Output directory where processed data located.")
flags.DEFINE_string("model_dir", None, "Model directory where checkpoints located.")
flags.DEFINE_string("export_dir", None, "Export directory where saved model located.")

flags.DEFINE_string("task_name", default=None, help="The name of the task to train.")
flags.DEFINE_string("model_config_path", default=None, help="Config file of the pre-trained model.")
flags.DEFINE_string("init_checkpoint", default=None, help="Initial checkpoint of the pre-trained model.")
flags.DEFINE_string("spiece_model_file", default=None, help="Sentence Piece model path.")
flags.DEFINE_bool("overwrite_data", default=False, help="If False, will use cached data if available.")
flags.DEFINE_integer("random_seed", default=100, help="Random seed for weight initialzation.")
flags.DEFINE_string("predict_tag", None, "Predict tag for predict result tracking.")

flags.DEFINE_bool("do_train", default=False, help="Whether to run training.")
flags.DEFINE_bool("do_predict", default=False, help="Whether to run prediction.")
flags.DEFINE_bool("do_export", default=False, help="Whether to run exporting.")

flags.DEFINE_enum("init", default="normal", enum_values=["normal", "uniform"], help="Initialization method.")
flags.DEFINE_float("init_std", default=0.02, help="Initialization std when init is normal.")
flags.DEFINE_float("init_range", default=0.1, help="Initialization std when init is uniform.")
flags.DEFINE_bool("init_global_vars", default=False, help="If true, init all global vars. If false, init trainable vars only.")

flags.DEFINE_bool("lower_case", default=False, help="Enable lower case nor not.")
flags.DEFINE_integer("doc_stride", default=128, help="Doc stride")
flags.DEFINE_integer("max_seq_length", default=512, help="Max sequence length")
flags.DEFINE_integer("max_query_length", default=64, help="Max query length")
flags.DEFINE_integer("max_answer_length", default=64, help="Max answer length")
flags.DEFINE_integer("train_batch_size", default=48, help="Total batch size for training.")
flags.DEFINE_integer("predict_batch_size", default=32, help="Total batch size for predict.")

flags.DEFINE_integer("train_steps", default=8000, help="Number of training steps")
flags.DEFINE_integer("warmup_steps", default=0, help="number of warmup steps")
flags.DEFINE_integer("max_save", default=5, help="Max number of checkpoints to save. Use 0 to save all.")
flags.DEFINE_integer("save_steps", default=1000, help="Save the model for every save_steps. If None, not to save any model.")
flags.DEFINE_integer("shuffle_buffer", default=2048, help="Buffer size used for shuffle.")

flags.DEFINE_integer("n_best_size", default=5, help="n best size for predictions")
flags.DEFINE_integer("start_n_top", default=5, help="Beam size for span start.")
flags.DEFINE_integer("end_n_top", default=5, help="Beam size for span end.")
flags.DEFINE_string("target_eval_key", default="best_f1", help="Use has_ans_f1 for Model I.")

flags.DEFINE_bool("use_bfloat16", default=False, help="Whether to use bfloat16.")
flags.DEFINE_float("dropout", default=0.1, help="Dropout rate.")
flags.DEFINE_float("dropatt", default=0.1, help="Attention dropout rate.")
flags.DEFINE_integer("clamp_len", default=-1, help="Clamp length")
flags.DEFINE_string("summary_type", default="last", help="Method used to summarize a sequence into a vector.")

flags.DEFINE_float("learning_rate", default=3e-5, help="initial learning rate")
flags.DEFINE_float("min_lr_ratio", default=0.0, help="min lr ratio for cos decay.")
flags.DEFINE_float("lr_layer_decay_rate", default=0.75, help="lr[L] = learning_rate, lr[l-1] = lr[l] * lr_layer_decay_rate.")
flags.DEFINE_float("clip", default=1.0, help="Gradient clipping")
flags.DEFINE_float("weight_decay", default=0.00, help="Weight decay rate")
flags.DEFINE_float("adam_epsilon", default=1e-6, help="Adam epsilon")
flags.DEFINE_string("decay_method", default="poly", help="poly or cos")

flags.DEFINE_bool("use_tpu", False, "Whether to use TPU or GPU/CPU.")
flags.DEFINE_integer("num_hosts", 1, "How many TPU hosts.")
flags.DEFINE_integer("num_core_per_host", 1, "Total number of TPU cores to use.")
flags.DEFINE_string("tpu_job_name", None, "TPU worker job name.")
flags.DEFINE_string("tpu", None, "The Cloud TPU name to use for training.")
flags.DEFINE_string("tpu_zone", None, "GCE zone where the Cloud TPU is located in.")
flags.DEFINE_string("gcp_project", None, "Project name for the Cloud TPU-enabled project.")
flags.DEFINE_string("master", None, "TensorFlow master URL")
flags.DEFINE_integer("iterations", 1000, "number of iterations per TPU training loop.")

class InputExample(object):
    """A single SQuAD example."""
    def __init__(self,
                 qas_id,
                 question_text,
                 paragraph_text,
                 orig_answer_text=None,
                 start_position=None,
                 is_impossible=False):
        self.qas_id = qas_id
        self.question_text = question_text
        self.paragraph_text = paragraph_text
        self.orig_answer_text = orig_answer_text
        self.start_position = start_position
        self.is_impossible = is_impossible
    
    def __str__(self):
        return self.__repr__()
    
    def __repr__(self):
        s = "qas_id: %s" % (prepro_utils.printable_text(self.qas_id))
        s += ", question_text: %s" % (prepro_utils.printable_text(self.question_text))
        s += ", paragraph_text: [%s]" % (" ".join(self.paragraph_text))
        if self.start_position:
            s += ", start_position: %d" % (self.start_position)
            s += ", is_impossible: %r" % (self.is_impossible)
        return s

class InputFeatures(object):
    """A single SQuAD feature."""
    def __init__(self,
                 unique_id,
                 qas_id,
                 doc_idx,
                 token2char_raw_start_index,
                 token2char_raw_end_index,
                 token2doc_index,
                 input_ids,
                 input_mask,
                 p_mask,
                 segment_ids,
                 cls_index,
                 para_length,
                 start_position=None,
                 end_position=None,
                 is_impossible=None):
        self.unique_id = unique_id
        self.qas_id = qas_id
        self.doc_idx = doc_idx
        self.token2char_raw_start_index = token2char_raw_start_index
        self.token2char_raw_end_index = token2char_raw_end_index
        self.token2doc_index = token2doc_index
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.p_mask = p_mask
        self.segment_ids = segment_ids
        self.cls_index = cls_index
        self.para_length = para_length
        self.start_position = start_position
        self.end_position = end_position
        self.is_impossible = is_impossible

class OutputResult(object):
    """A single SQuAD result."""
    def __init__(self,
                 unique_id,
                 answer_prob,
                 start_prob,
                 start_index,
                 end_prob,
                 end_index):
        self.unique_id = unique_id
        self.answer_prob = answer_prob
        self.start_prob = start_prob
        self.start_index = start_index
        self.end_prob = end_prob
        self.end_index = end_index

class SquadPipeline(object):
    """Pipeline for SQuAD dataset."""
    def __init__(self,
                 data_dir,
                 task_name):
        self.data_dir = data_dir
        self.task_name = task_name
    
    def get_train_examples(self):
        """Gets a collection of `InputExample`s for the train set."""
        data_path = os.path.join(self.data_dir, "train-{0}.json".format(self.task_name))
        data_list = self._read_json(data_path)
        example_list = self._get_example(data_list, True)
        return example_list
    
    def get_dev_examples(self):
        """Gets a collection of `InputExample`s for the dev set."""
        data_path = os.path.join(self.data_dir, "dev-{0}.json".format(self.task_name))
        data_list = self._read_json(data_path)
        example_list = self._get_example(data_list, False)
        return example_list
    
    def _read_json(self,
                   data_path):
        if os.path.exists(data_path):
            with open(data_path, "r") as file:
                data_list = json.load(file)["data"]
                return data_list
        else:
            raise FileNotFoundError("data path not found: {0}".format(data_path))
    
    def _get_example(self,
                     data_list,
                     is_training):
        examples = []
        for entry in data_list:
            for paragraph in entry["paragraphs"]:
                paragraph_text = paragraph["context"]
                
                for qa in paragraph["qas"]:
                    qas_id = qa["id"]
                    question_text = qa["question"]
                    start_position = None
                    orig_answer_text = None
                    is_impossible = False
                    
                    if is_training:
                        is_impossible = qa["is_impossible"]
                        if (len(qa["answers"]) != 1) and (not is_impossible):
                            raise ValueError("For training, each question should have exactly 1 answer.")
                        
                        if not is_impossible:
                            answer = qa["answers"][0]
                            orig_answer_text = answer["text"]
                            start_position = answer["answer_start"]
                        else:
                            start_position = -1
                            orig_answer_text = ""
                    
                    example = InputExample(
                        qas_id=qas_id,
                        question_text=question_text,
                        paragraph_text=paragraph_text,
                        orig_answer_text=orig_answer_text,
                        start_position=start_position,
                        is_impossible=is_impossible)
                    
                    examples.append(example)
        
        return examples

class XLNetTokenizer(object):
    """Default text tokenizer for XLNet"""
    def __init__(self,
                 sp_model_file,
                 lower_case=False):
        """Construct XLNet tokenizer"""
        self.sp_processor = sp.SentencePieceProcessor()
        self.sp_processor.Load(sp_model_file)
        self.lower_case = lower_case
    
    def tokenize(self,
                 text):
        """Tokenize text for XLNet"""
        processed_text = prepro_utils.preprocess_text(text, lower=self.lower_case)
        tokenized_pieces = prepro_utils.encode_pieces(self.sp_processor, processed_text, return_unicode=False)
        return tokenized_pieces
    
    def encode(self,
               text):
        """Encode text for XLNet"""
        processed_text = prepro_utils.preprocess_text(text, lower=self.lower_case)
        encoded_ids = prepro_utils.encode_ids(self.sp_processor, processed_text)
        return encoded_ids
    
    def token_to_id(self,
                    token):
        """Convert token to id for XLNet"""
        return self.sp_processor.PieceToId(token)
    
    def id_to_token(self,
                    id):
        """Convert id to token for XLNet"""
        return self.sp_processor.IdToPiece(id)
    
    def tokens_to_ids(self,
                      tokens):
        """Convert tokens to ids for XLNet"""
        return [self.sp_processor.PieceToId(token) for token in tokens]
    
    def ids_to_tokens(self,
                      ids):
        """Convert ids to tokens for XLNet"""
        return [self.sp_processor.IdToPiece(id) for id in ids]

class XLNetExampleProcessor(object):
    """Default example processor for XLNet"""
    def __init__(self,
                 max_seq_length,
                 max_query_length,
                 doc_stride,
                 tokenizer):
        """Construct XLNet example processor"""
        self.special_vocab_list = ["<unk>", "<s>", "</s>", "<cls>", "<sep>", "<pad>", "<mask>", "<eod>", "<eop>"]
        self.special_vocab_map = {}
        for (i, special_vocab) in enumerate(self.special_vocab_list):
            self.special_vocab_map[special_vocab] = i
        
        self.segment_vocab_list = ["<p>", "<q>", "<cls>", "<sep>", "<pad>"]
        self.segment_vocab_map = {}
        for (i, segment_vocab) in enumerate(self.segment_vocab_list):
            self.segment_vocab_map[segment_vocab] = i
                
        self.max_seq_length = max_seq_length
        self.max_query_length = max_query_length
        self.doc_stride = doc_stride
        self.tokenizer = tokenizer
        self.unique_id = 1000000000
    
    def _generate_match_mapping(self,
                                para_text,
                                tokenized_para_text,
                                N,
                                M,
                                max_N,
                                max_M):
        """Generate match mapping for raw and tokenized paragraph"""
        def _lcs_match(para_text,
                       tokenized_para_text,
                       N,
                       M,
                       max_N,
                       max_M,
                       max_dist):
            """longest common sub-sequence
            
            f[i, j] = max(f[i - 1, j], f[i, j - 1], f[i - 1, j - 1] + match(i, j))
            
            unlike standard LCS, this is specifically optimized for the setting
            because the mismatch between sentence pieces and original text will be small
            """
            f = np.zeros((max_N, max_M), dtype=np.float32)
            g = {}
            
            for i in range(N):
                for j in range(i - max_dist, i + max_dist):
                    if j >= M or j < 0:
                        continue
                    
                    if i > 0:
                        g[(i, j)] = 0
                        f[i, j] = f[i - 1, j]
                    
                    if j > 0 and f[i, j - 1] > f[i, j]:
                        g[(i, j)] = 1
                        f[i, j] = f[i, j - 1]
                    
                    f_prev = f[i - 1, j - 1] if i > 0 and j > 0 else 0
                    
                    raw_char = prepro_utils.preprocess_text(para_text[i], lower=self.tokenizer.lower_case, remove_space=False)
                    tokenized_char = tokenized_para_text[j]
                    if (raw_char == tokenized_char and f_prev + 1 > f[i, j]):
                        g[(i, j)] = 2
                        f[i, j] = f_prev + 1
            
            return f, g
        
        max_dist = abs(N - M) + 5
        for _ in range(2):
            lcs_matrix, match_mapping = _lcs_match(para_text, tokenized_para_text, N, M, max_N, max_M, max_dist)
            
            if lcs_matrix[N - 1, M - 1] > 0.8 * N:
                break
            
            max_dist *= 2
        
        mismatch = lcs_matrix[N - 1, M - 1] < 0.8 * N
        return match_mapping, mismatch
    
    def _convert_tokenized_index(self,
                                 index,
                                 pos,
                                 M=None,
                                 is_start=True):
        """Convert index for tokenized text"""
        if index[pos] is not None:
            return index[pos]
        
        N = len(index)
        rear = pos
        while rear < N - 1 and index[rear] is None:
            rear += 1
        
        front = pos
        while front > 0 and index[front] is None:
            front -= 1
        
        assert index[front] is not None or index[rear] is not None
        
        if index[front] is None:
            if index[rear] >= 1:
                if is_start:
                    return 0
                else:
                    return index[rear] - 1
            
            return index[rear]
        
        if index[rear] is None:
            if M is not None and index[front] < M - 1:
                if is_start:
                    return index[front] + 1
                else:
                    return M - 1
            
            return index[front]
        
        if is_start:
            if index[rear] > index[front] + 1:
                return index[front] + 1
            else:
                return index[rear]
        else:
            if index[rear] > index[front] + 1:
                return index[rear] - 1
            else:
                return index[front]
    
    def _find_max_context(self,
                          doc_spans,
                          token_idx):
        """Check if this is the 'max context' doc span for the token.

        Because of the sliding window approach taken to scoring documents, a single
        token can appear in multiple documents. E.g.
          Doc: the man went to the store and bought a gallon of milk
          Span A: the man went to the
          Span B: to the store and bought
          Span C: and bought a gallon of
          ...
        
        Now the word 'bought' will have two scores from spans B and C. We only
        want to consider the score with "maximum context", which we define as
        the *minimum* of its left and right context (the *sum* of left and
        right context will always be the same, of course).
        
        In the example the maximum context for 'bought' would be span C since
        it has 1 left context and 3 right context, while span B has 4 left context
        and 0 right context.
        """
        best_doc_score = None
        best_doc_idx = None
        for (doc_idx, doc_span) in enumerate(doc_spans):
            doc_start = doc_span["start"]
            doc_length = doc_span["length"]
            doc_end = doc_start + doc_length - 1
            if token_idx < doc_start or token_idx > doc_end:
                continue
            
            left_context_length = token_idx - doc_start
            right_context_length = doc_end - token_idx
            doc_score = min(left_context_length, right_context_length) + 0.01 * doc_length
            if best_doc_score is None or doc_score > best_doc_score:
                best_doc_score = doc_score
                best_doc_idx = doc_idx
        
        return best_doc_idx
    
    def convert_squad_example(self,
                              example,
                              is_training=True,
                              logging=False):
        """Converts a single `InputExample` into a single `InputFeatures`."""
        query_tokens = self.tokenizer.tokenize(example.question_text)
        if len(query_tokens) > self.max_query_length:
            query_tokens = query_tokens[:self.max_query_length]
        
        para_text = example.paragraph_text
        para_tokens = self.tokenizer.tokenize(example.paragraph_text)
        
        char2token_index = []
        token2char_start_index = []
        token2char_end_index = []
        char_idx = 0
        for i, token in enumerate(para_tokens):
            char_len = len(token)
            char2token_index.extend([i] * char_len)
            token2char_start_index.append(char_idx)
            char_idx += char_len
            token2char_end_index.append(char_idx - 1)
        
        tokenized_para_text = ''.join(para_tokens).replace(prepro_utils.SPIECE_UNDERLINE, ' ')
        
        N, M = len(para_text), len(tokenized_para_text)
        max_N, max_M = 1024, 1024
        if N > max_N or M > max_M:
            max_N = max(N, max_N)
            max_M = max(M, max_M)
        
        match_mapping, mismatch = self._generate_match_mapping(para_text, tokenized_para_text, N, M, max_N, max_M)
        
        raw2tokenized_char_index = [None] * N
        tokenized2raw_char_index = [None] * M
        i, j = N-1, M-1
        while i >= 0 and j >= 0:
            if (i, j) not in match_mapping:
                break
            
            if match_mapping[(i, j)] == 2:
                raw2tokenized_char_index[i] = j
                tokenized2raw_char_index[j] = i
                i, j = i - 1, j - 1
            elif match_mapping[(i, j)] == 1:
                j = j - 1
            else:
                i = i - 1
        
        if all(v is None for v in raw2tokenized_char_index) or mismatch:
            tf.logging.warning("raw and tokenized paragraph mismatch detected for example: %s" % example.qas_id)
        
        token2char_raw_start_index = []
        token2char_raw_end_index = []
        for idx in range(len(para_tokens)):
            start_pos = token2char_start_index[idx]
            end_pos = token2char_end_index[idx]
            raw_start_pos = self._convert_tokenized_index(tokenized2raw_char_index, start_pos, N, is_start=True)
            raw_end_pos = self._convert_tokenized_index(tokenized2raw_char_index, end_pos, N, is_start=False)
            token2char_raw_start_index.append(raw_start_pos)
            token2char_raw_end_index.append(raw_end_pos)

        if is_training:
            if not example.is_impossible:
                raw_start_char_pos = example.start_position
                raw_end_char_pos = raw_start_char_pos + len(example.orig_answer_text) - 1
                tokenized_start_char_pos = self._convert_tokenized_index(raw2tokenized_char_index, raw_start_char_pos, is_start=True)
                tokenized_end_char_pos = self._convert_tokenized_index(raw2tokenized_char_index, raw_end_char_pos, is_start=False)
                tokenized_start_token_pos = char2token_index[tokenized_start_char_pos]
                tokenized_end_token_pos = char2token_index[tokenized_end_char_pos]
                assert tokenized_start_token_pos <= tokenized_end_token_pos
            else:
                tokenized_start_token_pos = tokenized_end_token_pos = -1
        else:
            tokenized_start_token_pos = tokenized_end_token_pos = None
        
        # The -3 accounts for [CLS], [SEP] and [SEP]
        max_para_length = self.max_seq_length - len(query_tokens) - 3
        total_para_length = len(para_tokens)
        
        # We can have documents that are longer than the maximum sequence length.
        # To deal with this we do a sliding window approach, where we take chunks
        # of the up to our max length with a stride of `doc_stride`.
        doc_spans = []
        para_start = 0
        while para_start < total_para_length:
            para_length = total_para_length - para_start
            if para_length > max_para_length:
                para_length = max_para_length
            
            doc_spans.append({
                "start": para_start,
                "length": para_length
            })
            
            if para_start + para_length == total_para_length:
                break
            
            para_start += min(para_length, self.doc_stride)
        
        feature_list = []
        for (doc_idx, doc_span) in enumerate(doc_spans):
            input_tokens = []
            segment_ids = []
            p_mask = []
            doc_token2char_raw_start_index = []
            doc_token2char_raw_end_index = []
            doc_token2doc_index = {}
            
            for i in range(doc_span["length"]):
                token_idx = doc_span["start"] + i
                
                doc_token2char_raw_start_index.append(token2char_raw_start_index[token_idx])
                doc_token2char_raw_end_index.append(token2char_raw_end_index[token_idx])
                
                best_doc_idx = self._find_max_context(doc_spans, token_idx)
                doc_token2doc_index[len(input_tokens)] = (best_doc_idx == doc_idx)
                
                input_tokens.append(para_tokens[token_idx])
                segment_ids.append(self.segment_vocab_map["<p>"])
                p_mask.append(0)
            
            doc_para_length = len(input_tokens)
            
            input_tokens.append("<sep>")
            segment_ids.append(self.segment_vocab_map["<p>"])
            p_mask.append(1)
            
            # We put P before Q because during pretraining, B is always shorter than A
            for query_token in query_tokens:
                input_tokens.append(query_token)
                segment_ids.append(self.segment_vocab_map["<q>"])
                p_mask.append(1)

            input_tokens.append("<sep>")
            segment_ids.append(self.segment_vocab_map["<q>"])
            p_mask.append(1)
            
            cls_index = len(input_tokens)
            
            input_tokens.append("<cls>")
            segment_ids.append(self.segment_vocab_map["<cls>"])
            p_mask.append(0)
            
            input_ids = self.tokenizer.tokens_to_ids(input_tokens)
            
            # The mask has 0 for real tokens and 1 for padding tokens. Only real tokens are attended to.
            input_mask = [0] * len(input_ids)
            
            # Zero-pad up to the sequence length.
            while len(input_ids) < self.max_seq_length:
                input_ids.append(self.special_vocab_map["<pad>"])
                input_mask.append(1)
                segment_ids.append(self.segment_vocab_map["<pad>"])
                p_mask.append(1)
            
            assert len(input_ids) == self.max_seq_length
            assert len(input_mask) == self.max_seq_length
            assert len(segment_ids) == self.max_seq_length
            assert len(p_mask) == self.max_seq_length
            
            start_position = None
            end_position = None
            is_impossible = example.is_impossible
            if is_training:
                if not is_impossible:
                    # For training, if our document chunk does not contain an annotation, set default values.
                    doc_start = doc_span["start"]
                    doc_end = doc_start + doc_span["length"] - 1
                    if tokenized_start_token_pos < doc_start or tokenized_end_token_pos > doc_end:
                        start_position = 0
                        end_position = 0
                        is_impossible = True
                    else:
                        start_position = tokenized_start_token_pos - doc_start
                        end_position = tokenized_end_token_pos - doc_start
                else:
                    start_position = cls_index
                    end_position = cls_index
            
            if logging:
                tf.logging.info("*** Example ***")
                tf.logging.info("unique_id: %s" % str(self.unique_id))
                tf.logging.info("qas_id: %s" % example.qas_id)
                tf.logging.info("doc_idx: %s" % str(doc_idx))
                tf.logging.info("doc_token2char_raw_start_index: %s" % " ".join([str(x) for x in doc_token2char_raw_start_index]))
                tf.logging.info("doc_token2char_raw_end_index: %s" % " ".join([str(x) for x in doc_token2char_raw_end_index]))
                tf.logging.info("doc_token2doc_index: %s" % " ".join(["%d:%s" % (x, y) for (x, y) in doc_token2doc_index.items()]))
                tf.logging.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
                tf.logging.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
                tf.logging.info("p_mask: %s" % " ".join([str(x) for x in p_mask]))
                tf.logging.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
                if is_training:
                    if not is_impossible:
                        tf.logging.info("start_position: %s" % str(start_position))
                        tf.logging.info("end_position: %s" % str(end_position))
                        answer_tokens = input_tokens[start_position:end_position+1]
                        answer_text = prepro_utils.printable_text("".join(answer_tokens).replace(prepro_utils.SPIECE_UNDERLINE, " "))
                        tf.logging.info("answer_text: %s" % answer_text)
                    else:
                        tf.logging.info("impossible example")
                
            feature = InputFeatures(
                unique_id=self.unique_id,
                qas_id=example.qas_id,
                doc_idx=doc_idx,
                token2char_raw_start_index=doc_token2char_raw_start_index,
                token2char_raw_end_index=doc_token2char_raw_end_index,
                token2doc_index=doc_token2doc_index,
                input_ids=input_ids,
                input_mask=input_mask,
                p_mask=p_mask,
                segment_ids=segment_ids,
                cls_index=cls_index,
                para_length=doc_para_length,
                start_position=start_position,
                end_position=end_position,
                is_impossible=is_impossible)
            
            feature_list.append(feature)
            self.unique_id += 1
        
        return feature_list
    
    def convert_examples_to_features(self,
                                     examples,
                                     is_training=True):
        """Convert a set of `InputExample`s to a list of `InputFeatures`."""
        features = []
        for (idx, example) in enumerate(examples):
            if idx % 1000 == 0:
                tf.logging.info("Writing example %d of %d" % (idx, len(examples)))

            feature_list = self.convert_squad_example(example, is_training, logging=(idx < 20))
            features.extend(feature_list)

        return features
    
    def save_features_as_tfrecord(self,
                                  features,
                                  output_file,
                                  is_training=True):
        """Save a set of `InputFeature`s to a TFRecord file."""
        def create_int_feature(values):
            return tf.train.Feature(int64_list=tf.train.Int64List(value=list(values)))
        
        def create_float_feature(values):
            return tf.train.Feature(float_list=tf.train.FloatList(value=list(values)))
        
        with tf.python_io.TFRecordWriter(output_file) as writer:
            for feature in features:
                features = collections.OrderedDict()
                features["unique_id"] = create_int_feature([feature.unique_id])
                features["input_ids"] = create_int_feature(feature.input_ids)
                features["input_mask"] = create_float_feature(feature.input_mask)
                features["p_mask"] = create_float_feature(feature.p_mask)
                features["segment_ids"] = create_int_feature(feature.segment_ids)
                features["cls_index"] = create_int_feature([feature.cls_index])
                
                if is_training == True:
                    features["start_position"] = create_int_feature([feature.start_position])
                    features["end_position"] = create_int_feature([feature.end_position])
                    features["is_impossible"] = create_float_feature([1 if feature.is_impossible else 0])
                
                tf_example = tf.train.Example(features=tf.train.Features(feature=features))
                writer.write(tf_example.SerializeToString())
    
    def save_features_as_pickle(self,
                                features,
                                output_file):
        """Save a set of `InputFeature`s to a Pickle file."""
        with open(output_file, 'wb') as file:
            pickle.dump(features, file)
    
    def load_features_from_pickle(self,
                                  input_file):
        """Load a set of `InputFeature`s from a Pickle file."""
        if not os.path.exists(input_file):
            raise FileNotFoundError("feature file not found: {0}".format(input_file))
        
        with open(input_file, 'rb') as file:
            features = pickle.load(file)
            return features

class XLNetInputBuilder(object):
    """Default input builder for XLNet"""
    @staticmethod
    def get_input_fn(input_file,
                     seq_length,
                     is_training,
                     drop_remainder,
                     shuffle_buffer=2048,
                     num_threads=16):
        """Creates an `input_fn` closure to be passed to TPUEstimator."""
        name_to_features = {
            "unique_id": tf.FixedLenFeature([], tf.int64),
            "input_ids": tf.FixedLenFeature([seq_length], tf.int64),
            "input_mask": tf.FixedLenFeature([seq_length], tf.float32),
            "p_mask": tf.FixedLenFeature([seq_length], tf.float32),
            "segment_ids": tf.FixedLenFeature([seq_length], tf.int64),
            "cls_index": tf.FixedLenFeature([], tf.int64),
        }
        
        if is_training:
            name_to_features["start_position"] = tf.FixedLenFeature([], tf.int64)
            name_to_features["end_position"] = tf.FixedLenFeature([], tf.int64)
            name_to_features["is_impossible"] = tf.FixedLenFeature([], tf.float32)
        
        def _decode_record(record,
                           name_to_features):
            """Decodes a record to a TensorFlow example."""
            example = tf.parse_single_example(record, name_to_features)
            
            # tf.Example only supports tf.int64, but the TPU only supports tf.int32. So cast all int64 to int32.
            for name in list(example.keys()):
                t = example[name]
                if t.dtype == tf.int64:
                    t = tf.to_int32(t)
                example[name] = t

            return example
        
        def input_fn(params):
            """The actual input function."""
            batch_size = params["batch_size"]
            
            # For training, we want a lot of parallel reading and shuffling.
            # For eval, we want no shuffling and parallel reading doesn't matter.
            d = tf.data.TFRecordDataset(input_file)
            
            if is_training:
                d = d.repeat()
                d = d.shuffle(buffer_size=shuffle_buffer, seed=np.random.randint(10000))
            
            d = d.apply(tf.contrib.data.map_and_batch(
                lambda record: _decode_record(record, name_to_features),
                batch_size=batch_size,
                num_parallel_batches=num_threads,
                drop_remainder=drop_remainder))
            
            return d.prefetch(1024)
        
        return input_fn
    
    @staticmethod
    def get_serving_input_fn(seq_length):
        """Creates an `input_fn` closure to be passed to TPUEstimator."""
        def serving_input_fn():
            with tf.variable_scope("serving"):
                features = {
                    'unique_id': tf.placeholder(tf.int32, [None], name='unique_id'),
                    'input_ids': tf.placeholder(tf.int32, [None, seq_length], name='input_ids'),
                    'input_mask': tf.placeholder(tf.float32, [None, seq_length], name='input_mask'),
                    'p_mask': tf.placeholder(tf.float32, [None, seq_length], name='p_mask'),
                    'segment_ids': tf.placeholder(tf.int32, [None, seq_length], name='segment_ids'),
                    'cls_index': tf.placeholder(tf.int32, [None], name='cls_index'),
                }
                
                return tf.estimator.export.build_raw_serving_input_receiver_fn(features)()
        
        return serving_input_fn

class XLNetModelBuilder(object):
    """Default model builder for XLNet"""
    def __init__(self,
                 default_model_config,
                 default_run_config,
                 default_init_checkpoint,
                 use_tpu=False):
        """Construct XLNet model builder"""
        self.default_model_config = default_model_config
        self.default_run_config = default_run_config
        self.default_init_checkpoint = default_init_checkpoint
        self.use_tpu = use_tpu
    
    def _generate_masked_data(self,
                              input_data,
                              input_mask):
        """Generate masked data"""
        return input_data * input_mask + MIN_FLOAT * (1 - input_mask)
    
    def _generate_onehot_label(self,
                               input_data,
                               input_depth):
        """Generate one-hot label"""
        return tf.one_hot(input_data, depth=input_depth, on_value=1.0, off_value=0.0, dtype=tf.float32)
    
    def _compute_loss(self,
                      label,
                      label_mask,
                      predict,
                      predict_mask,
                      label_smoothing=0.0):
        """Compute optimization loss"""
        masked_predict = self._generate_masked_data(predict, predict_mask)
        masked_label = tf.cast(label, dtype=tf.int32) * tf.cast(label_mask, dtype=tf.int32)
                
        if label_smoothing > 1e-10:
            onehot_label = self._generate_onehot_label(masked_label, tf.shape(masked_predict)[-1])
            onehot_label = (onehot_label * (1 - label_smoothing) +
                label_smoothing / tf.cast(tf.shape(masked_predict)[-1], dtype=tf.float32)) * predict_mask
            loss = tf.nn.softmax_cross_entropy_with_logits_v2(labels=onehot_label, logits=masked_predict)
        else:
            loss = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=masked_label, logits=masked_predict)
        
        return loss
    
    def _create_model(self,
                      model_config,
                      run_config,
                      is_training,
                      input_ids,
                      input_mask,
                      p_mask,
                      segment_ids,
                      cls_index,
                      start_positions=None,
                      end_positions=None,
                      is_impossible=None):
        """Creates XLNet-MRC model"""        
        model = xlnet.XLNetModel(
            xlnet_config=model_config,
            run_config=run_config,
            input_ids=tf.transpose(input_ids, perm=[1,0]),                                                               # [b,l] --> [l,b]
            input_mask=tf.transpose(input_mask, perm=[1,0]),                                                             # [b,l] --> [l,b]
            seg_ids=tf.transpose(segment_ids, perm=[1,0]))                                                               # [b,l] --> [l,b]
        
        initializer = model.get_initializer()
        seq_len = tf.shape(input_ids)[-1]
        output_result = tf.transpose(model.get_sequence_output(), perm=[1,0,2])                                      # [l,b,h] --> [b,l,h]
        
        predicts = {}
        with tf.variable_scope("mrc", reuse=tf.AUTO_REUSE):
            with tf.variable_scope("start", reuse=tf.AUTO_REUSE):
                start_result = output_result                                                                                     # [b,l,h]
                start_result_mask = 1 - p_mask                                                                                     # [b,l]
                
                start_result = tf.layers.dense(start_result, units=1, activation=None,
                    use_bias=True, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                    kernel_regularizer=None, bias_regularizer=None, trainable=True, name="start_project")            # [b,l,h] --> [b,l,1]
                
                start_result = tf.squeeze(start_result, axis=-1)                                                       # [b,l,1] --> [b,l]
                start_result = self._generate_masked_data(start_result, start_result_mask)                        # [b,l], [b,l] --> [b,l]
                start_prob = tf.nn.softmax(start_result, axis=-1)                                                                  # [b,l]
                
                if not is_training:
                    start_top_prob, start_top_index = tf.nn.top_k(start_prob, k=FLAGS.start_n_top)                # [b,l] --> [b,k], [b,k]
                    predicts["start_prob"] = start_top_prob
                    predicts["start_index"] = start_top_index
            
            with tf.variable_scope("end", reuse=tf.AUTO_REUSE):
                if is_training:
                    # During training, compute the end logits based on the ground truth of the start position
                    start_index = self._generate_onehot_label(tf.expand_dims(start_positions, axis=-1), seq_len)         # [b] --> [b,1,l]
                    feat_result = tf.matmul(start_index, output_result)                                     # [b,1,l], [b,l,h] --> [b,1,h]
                    feat_result = tf.tile(feat_result, multiples=[1,seq_len,1])                                      # [b,1,h] --> [b,l,h]
                    
                    end_result = tf.concat([output_result, feat_result], axis=-1)                          # [b,l,h], [b,l,h] --> [b,l,2h]
                    end_result_mask = 1 - p_mask                                                                                   # [b,l]
                    
                    end_result = tf.layers.dense(end_result, units=model_config.d_model, activation=tf.tanh,
                        use_bias=True, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                        kernel_regularizer=None, bias_regularizer=None, trainable=True, name="end_modeling")        # [b,l,2h] --> [b,l,h]
                    
                    end_result = tf.contrib.layers.layer_norm(end_result, center=True, scale=True,
                        activation_fn=None, begin_norm_axis=-1, begin_params_axis=-1, trainable=True)                # [b,l,h] --> [b,l,h]
                    
                    end_result = tf.layers.dense(end_result, units=1, activation=None,
                        use_bias=True, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                        kernel_regularizer=None, bias_regularizer=None, trainable=True, name="end_project")          # [b,l,h] --> [b,l,1]
                    
                    end_result = tf.squeeze(end_result, axis=-1)                                                       # [b,l,1] --> [b,l]
                    end_result = self._generate_masked_data(end_result, end_result_mask)                          # [b,l], [b,l] --> [b,l]
                    end_prob = tf.nn.softmax(end_result, axis=-1)                                                                  # [b,l]
                else:
                    # During inference, compute the end logits based on beam search
                    start_index = self._generate_onehot_label(start_top_index, seq_len)                                # [b,k] --> [b,k,l]
                    feat_result = tf.matmul(start_index, output_result)                                     # [b,k,l], [b,l,h] --> [b,k,h]
                    feat_result = tf.expand_dims(feat_result, axis=1)                                              # [b,k,h] --> [b,1,k,h]
                    feat_result = tf.tile(feat_result, multiples=[1,seq_len,1,1])                                # [b,1,k,h] --> [b,l,k,h]
                    
                    end_result = tf.expand_dims(output_result, axis=-2)                                            # [b,l,h] --> [b,l,1,h]
                    end_result = tf.tile(end_result, multiples=[1,1,FLAGS.start_n_top,1])                        # [b,l,1,h] --> [b,l,k,h]
                    end_result = tf.concat([end_result, feat_result], axis=-1)                       # [b,l,k,h], [b,l,k,h] --> [b,l,k,2h]
                    end_result_mask = tf.expand_dims(1 - p_mask, axis=1)                                               # [b,l] --> [b,1,l]
                    end_result_mask = tf.tile(end_result_mask, multiples=[1,FLAGS.start_n_top,1])                    # [b,1,l] --> [b,k,l]
                    
                    end_result = tf.layers.dense(end_result, units=model_config.d_model, activation=tf.tanh,
                        use_bias=True, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                        kernel_regularizer=None, bias_regularizer=None, trainable=True, name="end_modeling")    # [b,l,k,2h] --> [b,l,k,h]
                    
                    end_result = tf.contrib.layers.layer_norm(end_result, center=True, scale=True,
                        activation_fn=None, begin_norm_axis=-1, begin_params_axis=-1, trainable=True)            # [b,l,k,h] --> [b,l,k,h]
                    
                    end_result = tf.layers.dense(end_result, units=1, activation=None,
                        use_bias=True, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                        kernel_regularizer=None, bias_regularizer=None, trainable=True, name="end_project")      # [b,l,k,h] --> [b,l,k,1]
                    
                    end_result = tf.transpose(tf.squeeze(end_result, axis=-1), perm=[0,2,1])                       # [b,l,k,1] --> [b,k,l]
                    end_result = self._generate_masked_data(end_result, end_result_mask)                    # [b,k,l], [b,k,l] --> [b,k,l]
                    end_prob = tf.nn.softmax(end_result, axis=-1)                                                                # [b,k,l]
                    
                    end_top_prob, end_top_index = tf.nn.top_k(end_prob, k=FLAGS.end_n_top)                  # [b,k,l] --> [b,k,k], [b,k,k]
                    predicts["end_prob"] = end_top_prob
                    predicts["end_index"] = end_top_index
            
            with tf.variable_scope("answer", reuse=tf.AUTO_REUSE):
                cls_index = self._generate_onehot_label(tf.expand_dims(cls_index, axis=-1), seq_len)                     # [b] --> [b,1,l]
                feat_result = tf.matmul(tf.expand_dims(start_prob, axis=1), output_result)                    # [b,l], [b,l,h] --> [b,1,h]
                
                answer_result = tf.matmul(cls_index, output_result)                                         # [b,1,l], [b,l,h] --> [b,1,h]
                answer_result = tf.squeeze(tf.concat([feat_result, answer_result], axis=-1), axis=1)         # [b,1,h], [b,1,h] --> [b,2h]
                answer_result_mask = tf.reduce_max(1 - p_mask, axis=-1)                                                    # [b,l] --> [b]
                
                answer_result = tf.layers.dense(answer_result, units=model_config.d_model, activation=None,
                    use_bias=False, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                    kernel_regularizer=None, bias_regularizer=None, trainable=True, name="answer_modeling")             # [b,2h] --> [b,h]
                
                answer_result = tf.layers.dropout(answer_result,
                    rate=FLAGS.dropout, seed=np.random.randint(10000), training=is_training)                             # [b,h] --> [b,h]
                
                answer_result = tf.layers.dense(answer_result, units=1, activation=None,
                    use_bias=False, kernel_initializer=initializer, bias_initializer=tf.zeros_initializer,
                    kernel_regularizer=None, bias_regularizer=None, trainable=True, name="answer_project")               # [b,h] --> [b,1]
                
                answer_result = tf.squeeze(answer_result, axis=-1)                                                         # [b,1] --> [b]
                answer_result = self._generate_masked_data(answer_result, answer_result_mask)                           # [b], [b] --> [b]
                answer_prob = tf.sigmoid(answer_result)                                                                              # [b]
                predicts["answer_prob"] = answer_prob
            
            with tf.variable_scope("loss", reuse=tf.AUTO_REUSE):
                loss = tf.constant(0.0, dtype=tf.float32)
                if is_training:
                    start_label = start_positions                                                                                    # [b]
                    start_label_mask = tf.reduce_max(1 - p_mask, axis=-1)                                                  # [b,l] --> [b]
                    start_loss = self._compute_loss(start_label, start_label_mask, start_result, start_result_mask)                  # [b]
                    end_label = end_positions                                                                                        # [b]
                    end_label_mask = tf.reduce_max(1 - p_mask, axis=-1)                                                    # [b,l] --> [b]
                    end_loss = self._compute_loss(end_label, end_label_mask, end_result, end_result_mask)                            # [b]
                    loss += tf.reduce_mean(start_loss + end_loss) * 0.5
                    
                    if is_impossible is not None:
                        answer_label = is_impossible                                                                                 # [b]
                        answer_label_mask = tf.reduce_max(1 - p_mask, axis=-1)                                             # [b,l] --> [b]
                        answer_loss = tf.nn.sigmoid_cross_entropy_with_logits(
                            labels=answer_label * answer_label_mask, logits=answer_result)                                           # [b]
                        loss += tf.reduce_mean(answer_loss)
        
        return loss, predicts
    
    def get_model_fn(self,
                     model_config,
                     run_config,
                     init_checkpoint):
        """Returns `model_fn` closure for TPUEstimator."""
        def model_fn(features,
                     labels,
                     mode,
                     params):  # pylint: disable=unused-argument
            """The `model_fn` for TPUEstimator."""
            tf.logging.info("*** Features ***")
            for name in sorted(features.keys()):
                tf.logging.info("  name = %s, shape = %s" % (name, features[name].shape))
            
            is_training = (mode == tf.estimator.ModeKeys.TRAIN)
            
            unique_id = features["unique_id"]
            input_ids = features["input_ids"]
            input_mask = features["input_mask"]
            p_mask = features["p_mask"]
            segment_ids = features["segment_ids"]
            cls_index = features["cls_index"]
            
            if is_training:
                start_position = features["start_position"]
                end_position = features["end_position"]
                is_impossible = features["is_impossible"]
            else:
                start_position = None
                end_position = None
                is_impossible = None

            loss, predicts = self._create_model(model_config, run_config, is_training,
                input_ids, input_mask, p_mask, segment_ids, cls_index, start_position, end_position, is_impossible)
            
            scaffold_fn = model_utils.init_from_checkpoint(FLAGS)
            
            output_spec = None
            if is_training:
                train_op, _, _ = model_utils.get_train_op(FLAGS, loss)
                output_spec = tf.contrib.tpu.TPUEstimatorSpec(
                    mode=mode,
                    loss=loss,
                    train_op=train_op,
                    scaffold_fn=scaffold_fn)
            else:
                output_spec = tf.contrib.tpu.TPUEstimatorSpec(
                    mode=mode,
                    predictions={
                        "unique_id": unique_id,
                        "answer_prob": predicts["answer_prob"],
                        "start_prob": predicts["start_prob"],
                        "start_index": predicts["start_index"],
                        "end_prob": predicts["end_prob"],
                        "end_index": predicts["end_index"]
                    },
                    scaffold_fn=scaffold_fn)
            
            return output_spec
        
        return model_fn

class XLNetPredictProcessor(object):
    """Default predict processor for XLNet"""
    def __init__(self,
                 output_dir,
                 n_best_size,
                 start_n_top,
                 end_n_top,
                 max_answer_length,
                 tokenizer,
                 predict_tag=None):
        """Construct XLNet predict processor"""
        self.n_best_size = n_best_size
        self.start_n_top = start_n_top
        self.end_n_top = end_n_top
        self.max_answer_length = max_answer_length
        self.tokenizer = tokenizer
        
        predict_tag = predict_tag if predict_tag else str(time.time())
        self.output_summary = os.path.join(output_dir, "predict.{0}.summary.json".format(predict_tag))
        self.output_detail = os.path.join(output_dir, "predict.{0}.detail.json".format(predict_tag))
    
    def _write_to_json(self,
                       data_list,
                       data_path):
        data_folder = os.path.dirname(data_path)
        if not os.path.exists(data_folder):
            os.mkdir(data_folder)

        with open(data_path, "w") as file:  
            json.dump(data_list, file, indent=4)
    
    def _write_to_text(self,
                       data_list,
                       data_path):
        data_folder = os.path.dirname(data_path)
        if not os.path.exists(data_folder):
            os.mkdir(data_folder)

        with open(data_path, "w") as file:
            for data in data_list:
                file.write("{0}\n".format(data))
    
    def process(self,
                examples,
                features,
                results):
        qas_id_to_features = {}
        unique_id_to_feature = {}
        for feature in features:
            if feature.qas_id not in qas_id_to_features:
                qas_id_to_features[feature.qas_id] = []
            
            qas_id_to_features[feature.qas_id].append(feature)
            unique_id_to_feature[feature.unique_id] = feature
        
        unique_id_to_result = {}
        for result in results:
            unique_id_to_result[result.unique_id] = result
        
        predict_summary_list = []
        predict_detail_list = []
        num_example = len(examples)
        for (example_idx, example) in enumerate(examples):
            if example_idx % 1000 == 0:
                tf.logging.info('Updating {0}/{1} example with predict'.format(example_idx, num_example))
            
            if example.qas_id not in qas_id_to_features:
                tf.logging.warning('No feature found for example: {0}'.format(example.qas_id))
                continue
            
            example_answer_prob = MAX_FLOAT
            example_all_predicts = []
            example_features = qas_id_to_features[example.qas_id]
            for example_feature in example_features:
                if example_feature.unique_id not in unique_id_to_result:
                    tf.logging.warning('No result found for feature: {0}'.format(example_feature.unique_id))
                    continue
                
                example_result = unique_id_to_result[example_feature.unique_id]
                example_answer_prob = min(example_answer_prob, float(example_result.answer_prob))
                for i in range(self.start_n_top):
                    start_prob = example_result.start_prob[i]
                    start_index = example_result.start_index[i]
                    
                    for j in range(self.end_n_top):
                        end_prob = example_result.end_prob[i][j]
                        end_index = example_result.end_index[i][j]
                        
                        answer_length = end_index - start_index + 1
                        if end_index < start_index or answer_length > self.max_answer_length:
                            continue
                        
                        if start_index > example_feature.para_length or end_index > example_feature.para_length:
                            continue
                        
                        if start_index not in example_feature.token2doc_index:
                            continue
                        
                        example_all_predicts.append({
                            "unique_id": example_result.unique_id,
                            "start_prob": start_prob,
                            "start_index": start_index,
                            "end_prob": end_prob,
                            "end_index": end_index,
                            "predict_score": np.log(start_prob) + np.log(end_prob)
                        })
            
            example_all_predicts = sorted(example_all_predicts, key=lambda x: x["predict_score"], reverse=True)
            
            is_visited = set()
            example_top_predicts = []
            for example_predict in example_all_predicts:
                if len(example_top_predicts) >= self.n_best_size:
                    break
                
                example_feature = unique_id_to_feature[example_predict["unique_id"]]
                predict_start = example_feature.token2char_raw_start_index[example_predict["start_index"]]
                predict_end = example_feature.token2char_raw_end_index[example_predict["end_index"]]
                predict_text = example.paragraph_text[predict_start:predict_end + 1].strip()
                
                if predict_text in is_visited:
                    continue
                
                is_visited.add(predict_text)
                
                example_top_predicts.append({
                    "predict_text": predict_text,
                    "start_prob": float(example_predict["start_prob"]),
                    "end_prob": float(example_predict["end_prob"]),
                    "predict_score": float(example_predict["predict_score"])
                })
            
            if len(example_top_predicts) == 0:
                example_top_predicts.append({
                    "predict_text": "",
                    "start_prob": 0.0,
                    "end_prob": 0.0,
                    "predict_score": 0.0
                })
            
            example_best_predict = example_top_predicts[0]
            
            predict_summary_list.append({
                "qas_id": example.qas_id,
                "answer_prob": example_answer_prob,
                "start_prob": example_best_predict["start_prob"],
                "end_prob": example_best_predict["end_prob"],
                "predict_text": example_best_predict["predict_text"]
            })
                                          
            predict_detail_list.append({
                "qas_id": example.qas_id,
                "answer_prob": example_answer_prob,
                "best_predict": example_best_predict,
                "top_predicts": example_top_predicts,
            })
        
        self._write_to_json(predict_summary_list, self.output_summary)
        self._write_to_json(predict_detail_list, self.output_detail)

def main(_):
    tf.logging.set_verbosity(tf.logging.INFO)
    
    np.random.seed(FLAGS.random_seed)
    
    if not os.path.exists(FLAGS.output_dir):
        os.mkdir(FLAGS.output_dir)
    
    task_name = FLAGS.task_name.lower()
    data_pipeline = SquadPipeline(
        data_dir=FLAGS.data_dir,
        task_name=task_name)
    
    tpu_config = model_utils.configure_tpu(FLAGS)
    model_config = xlnet.XLNetConfig(json_path=FLAGS.model_config_path)
    run_config = xlnet.create_run_config(False, True, FLAGS)
    
    model_builder = XLNetModelBuilder(
        default_model_config=model_config,
        default_run_config=run_config,
        default_init_checkpoint=FLAGS.init_checkpoint,
        use_tpu=FLAGS.use_tpu)
    
    model_fn = model_builder.get_model_fn(model_config, run_config, FLAGS.init_checkpoint)
    
    # If TPU is not available, this will fall back to normal Estimator on CPU or GPU.
    estimator = tf.contrib.tpu.TPUEstimator(
        use_tpu=FLAGS.use_tpu,
        model_fn=model_fn,
        config=tpu_config,
        export_to_tpu=FLAGS.use_tpu,
        train_batch_size=FLAGS.train_batch_size,
        predict_batch_size=FLAGS.predict_batch_size)
    
    tokenizer = XLNetTokenizer(
        sp_model_file=FLAGS.spiece_model_file,
        lower_case=FLAGS.lower_case)
    
    example_processor = XLNetExampleProcessor(
        max_seq_length=FLAGS.max_seq_length,
        max_query_length=FLAGS.max_query_length,
        doc_stride=FLAGS.doc_stride,
        tokenizer=tokenizer)
    
    if FLAGS.do_train:
        train_examples = data_pipeline.get_train_examples()
        
        tf.logging.info("***** Run training *****")
        tf.logging.info("  Num examples = %d", len(train_examples))
        tf.logging.info("  Batch size = %d", FLAGS.train_batch_size)
        tf.logging.info("  Num steps = %d", FLAGS.train_steps)
        
        train_record_file = os.path.join(FLAGS.output_dir, "train-{0}.tfrecord".format(task_name))
        if not os.path.exists(train_record_file) or FLAGS.overwrite_data:
            train_features = example_processor.convert_examples_to_features(train_examples, True)
            np.random.shuffle(train_features)
            example_processor.save_features_as_tfrecord(train_features, train_record_file, True)
        
        train_input_fn = XLNetInputBuilder.get_input_fn(train_record_file, FLAGS.max_seq_length, True, True, FLAGS.shuffle_buffer)
        estimator.train(input_fn=train_input_fn, max_steps=FLAGS.train_steps)
    
    if FLAGS.do_predict:
        predict_examples = data_pipeline.get_dev_examples()
        
        tf.logging.info("***** Run prediction *****")
        tf.logging.info("  Num examples = %d", len(predict_examples))
        tf.logging.info("  Batch size = %d", FLAGS.predict_batch_size)
        
        predict_record_file = os.path.join(FLAGS.output_dir, "dev-{0}.tfrecord".format(task_name))
        predict_pickle_file = os.path.join(FLAGS.output_dir, "dev-{0}.pkl".format(task_name))
        if not os.path.exists(predict_record_file) or not os.path.exists(predict_pickle_file) or FLAGS.overwrite_data:
            predict_features = example_processor.convert_examples_to_features(predict_examples, False)
            example_processor.save_features_as_tfrecord(predict_features, predict_record_file, False)
            example_processor.save_features_as_pickle(predict_features, predict_pickle_file)
        else:
            predict_features = example_processor.load_features_from_pickle(predict_pickle_file)
        
        predict_input_fn = XLNetInputBuilder.get_input_fn(predict_record_file, FLAGS.max_seq_length, False, False)
        results = estimator.predict(input_fn=predict_input_fn)
        
        predict_results = [OutputResult(
            unique_id=result["unique_id"],
            answer_prob=result["answer_prob"],
            start_prob=result["start_prob"].tolist(),
            start_index=result["start_index"].tolist(),
            end_prob=result["end_prob"].tolist(),
            end_index=result["end_index"].tolist()
        ) for result in results]
        
        predict_processor = XLNetPredictProcessor(
            output_dir=FLAGS.output_dir,
            n_best_size=FLAGS.n_best_size,
            start_n_top=FLAGS.start_n_top,
            end_n_top=FLAGS.end_n_top,
            max_answer_length=FLAGS.max_answer_length,
            tokenizer=tokenizer,
            predict_tag=FLAGS.predict_tag)
        
        predict_processor.process(predict_examples, predict_features, predict_results)
    
    if FLAGS.do_export:
        tf.logging.info("***** Running exporting *****")
        if not os.path.exists(FLAGS.export_dir):
            os.mkdir(FLAGS.export_dir)
        
        serving_input_fn = XLNetInputBuilder.get_serving_input_fn(FLAGS.max_seq_length)
        estimator.export_savedmodel(FLAGS.export_dir, serving_input_fn, as_text=False)

if __name__ == "__main__":
    flags.mark_flag_as_required("spiece_model_file")
    flags.mark_flag_as_required("model_config_path")
    flags.mark_flag_as_required("init_checkpoint")
    flags.mark_flag_as_required("data_dir")
    flags.mark_flag_as_required("output_dir")
    flags.mark_flag_as_required("model_dir")
    flags.mark_flag_as_required("export_dir")
    tf.app.run()
