# coding=utf-8
# Copyright 2021 The Mesh TensorFlow Authors.
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import absltest
from absl.testing import parameterized
import mesh_tensorflow as mtf
from mesh_tensorflow.transformer import utils
import numpy as np
import tensorflow.compat.v1 as tf
import os
tf.disable_v2_behavior()


def mock_vocabulary(encode_dict, vocab_size=None):
  vocab = absltest.mock.MagicMock()
  vocab.vocab_size = vocab_size
  idx_to_str = {v: k for k, v in encode_dict.items()}
  vocab.decode = absltest.mock.MagicMock(
      side_effect=lambda ids: [idx_to_str[id] for id in ids])
  return vocab


class UtilsTest(parameterized.TestCase, tf.test.TestCase):

  def _mock_model_dir(self, checkpoint_steps):
    """Creates a mock dir with empty checkpoint files with real-looking names."""
    out_dir = self.create_tempdir()
    for step in checkpoint_steps:
      out_dir.create_file("model.ckpt-{}".format(step))
    return out_dir.full_path

  def testDynamicText2self_packed(self):
    batch = 2
    length = 5
    input_tensors = {
        "inputs": [[3, 1, 4, 1, 0], [1, 4, 3, 2, 1]],
        "inputs_segmentation": [[1, 1, 2, 2, 0], [1, 2, 2, 2, 2]],
        "inputs_position": [[0, 1, 0, 1, 0], [0, 0, 1, 2, 3]],
        "targets": [[1, 1, 0, 0, 0], [9, 8, 1, 2, 1]],
        "targets_segmentation": [[1, 2, 0, 0, 0], [1, 1, 1, 2, 2]],
        "targets_position": [[0, 0, 0, 0, 0], [0, 1, 2, 0, 1]]
    }
    expected_output_tensors = {
        "targets": [[3, 1, 1, 4, 1, 1, 0, 0, 0, 0],
                    [1, 9, 8, 1, 4, 3, 2, 1, 2, 1]],
        "targets_segmentation": [[1, 1, 1, 2, 2, 2, 0, 0, 0, 0],
                                 [1, 1, 1, 1, 2, 2, 2, 2, 2, 2]],
        "targets_position": [[0, 1, 2, 0, 1, 2, 0, 0, 0, 0],
                             [0, 1, 2, 3, 0, 1, 2, 3, 4, 5]]
    }
    graph = mtf.Graph()
    mesh = mtf.Mesh(graph, "my_mesh")
    batch_dim = mtf.Dimension("batch", batch)
    length_dim = mtf.Dimension("length", length)

    input_shape = mtf.Shape([batch_dim, length_dim])
    mtf_features = {
        k: mtf.import_tf_tensor(mesh, v, input_shape)
        for k, v in input_tensors.items()
    }
    mtf_outputs = utils._dynamic_text2self(mtf_features)
    mesh_impl = mtf.placement_mesh_impl.PlacementMeshImpl(
        shape=[], layout={}, devices=[""])
    lowering = mtf.Lowering(graph, {mesh: mesh_impl})
    for k, v in expected_output_tensors.items():
      out = lowering.export_to_tf_tensor(mtf_outputs[k])
      actual = self.evaluate(out)
      self.assertAllEqual(actual, v)

  def testDynamicText2self_unpacked(self):
    batch = 2
    length = 5
    input_tensors = {
        "inputs": [[3, 1, 4, 1, 0], [1, 4, 3, 2, 1]],
        "targets": [[1, 1, 0, 0, 0], [9, 8, 1, 2, 1]],
    }
    expected_output_tensors = {
        "targets": [[3, 1, 4, 1, 1, 1, 0, 0, 0, 0],
                    [1, 4, 3, 2, 1, 9, 8, 1, 2, 1]],
    }
    graph = mtf.Graph()
    mesh = mtf.Mesh(graph, "my_mesh")
    batch_dim = mtf.Dimension("batch", batch)
    length_dim = mtf.Dimension("length", length)

    input_shape = mtf.Shape([batch_dim, length_dim])
    mtf_features = {
        k: mtf.import_tf_tensor(mesh, v, input_shape)
        for k, v in input_tensors.items()
    }
    mtf_outputs = utils._dynamic_text2self(mtf_features)
    mesh_impl = mtf.placement_mesh_impl.PlacementMeshImpl(
        shape=[], layout={}, devices=[""])
    lowering = mtf.Lowering(graph, {mesh: mesh_impl})
    for k, v in expected_output_tensors.items():
      out = lowering.export_to_tf_tensor(mtf_outputs[k])
      actual = self.evaluate(out)
      self.assertAllEqual(actual, v)

  def testCleanDecodes(self):
    cleaned_decodes = utils.clean_decodes([[2, 0, 2, 1, 3, 2, 0],
                                           [1, 2, 2, 2, 2, 2, 2],
                                           [2, 2, 1, 1, 1, 1, 1],
                                           [2, 2, 2, 2, 2, 2, 2]])
    with self.test_session() as sess:
      self.assertAllEqual(
          sess.run(cleaned_decodes),
          [[2, 0, 2, 1, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0], [2, 2, 1, 0, 0, 0, 0],
           [2, 2, 2, 2, 2, 2, 2]])

  @parameterized.named_parameters(
      ("int16", np.int16),
      ("int32", np.int32),
      ("int64", np.int64),
  )
  def test_maybe_add_pretokenized_features_with_int_inputs(self, dtype):
    vocabulary = mock_vocabulary({"a": 1, "b": 2, "c": 3, "d": 4,},
                                 vocab_size=1000)

    examples = [{"targets": np.array([1, 2, 3, 4], dtype=dtype),
                 "inputs": np.array([1, 2, 3, 4], dtype=dtype)},
                ]
    result = utils._maybe_add_pretokenized_features(examples, vocabulary)
    expected = ["a", "b", "c", "d"]
    self.assertAllEqual(result[0]["targets_pretokenized"], expected)
    self.assertAllEqual(result[0]["inputs_pretokenized"], expected)
    self.assertLen(result, 1)

  def test_maybe_add_pretokenized_features_nonstandard_feature(self):
    vocabulary = mock_vocabulary({"a": 1, "b": 2, "c": 3, "d": 4,},
                                 vocab_size=1000)

    examples = [{"notafeature": np.array([1, 2, 3, 4], dtype=np.int32),
                 "inputs": np.array([1, 2, 3, 4], dtype=np.int32)}
                ]
    result = utils._maybe_add_pretokenized_features(examples, vocabulary)

    self.assertSameElements(result[0].keys(),
                            ("notafeature", "inputs", "inputs_pretokenized"))
    self.assertAllEqual(result[0]["notafeature"], [1, 2, 3, 4])

  def test_maybe_add_pretokenized_features_pretokenized_exists(self):
    vocabulary = mock_vocabulary({"a": 1, "b": 2, "c": 3, "d": 4,},
                                 vocab_size=1000)

    examples = [{"inputs_pretokenized": "Hello world!",
                 "inputs": np.array([1, 2, 3, 4], dtype=np.int32)}
                ]
    result = utils._maybe_add_pretokenized_features(examples, vocabulary)
    self.assertEqual(result[0]["inputs_pretokenized"], "Hello world!")
    self.assertSameElements(result[0].keys(), ("inputs", "inputs_pretokenized"))
    self.assertLen(result, 1)

  def test_checkpoint_iterator_step_not_exists(self):
    model_dir = self._mock_model_dir([10, 20])

    ckpt_paths = utils.get_checkpoint_iterator(12, model_dir, find_closest=True)
    expected = [os.path.join(model_dir, "model.ckpt-10")]
    self.assertAllEqual(expected, list(ckpt_paths), "closest is below")

    ckpt_paths = utils.get_checkpoint_iterator(18, model_dir, find_closest=True)
    expected = [os.path.join(model_dir, "model.ckpt-20")]
    self.assertAllEqual(expected, list(ckpt_paths), "closest is above")

    ckpt_paths = utils.get_checkpoint_iterator(
        [18, 19], model_dir, find_closest=True)
    expected = [os.path.join(model_dir, "model.ckpt-20")]
    self.assertAllEqual(expected, list(ckpt_paths),
                        "closest for both is step 20")

    ckpt_paths = utils.get_checkpoint_iterator(
        [12, 19], model_dir, find_closest=True)
    expected = [os.path.join(model_dir, "model.ckpt-10"),
                os.path.join(model_dir, "model.ckpt-20")]
    self.assertAllEqual(expected, list(ckpt_paths),
                        "closest two are steps 10 and 20")

    with self.assertRaises(ValueError,
                           msg="find_closest is false and step does not exist"):
      utils.get_checkpoint_iterator(12, model_dir, find_closest=False)

  def test_checkpoint_iterator_some_steps_exist(self):
    model_dir = self._mock_model_dir([10, 20])

    ckpt_paths = utils.get_checkpoint_iterator(10, model_dir, find_closest=True)
    expected = [os.path.join(model_dir, "model.ckpt-10")]
    self.assertAllEqual(expected, list(ckpt_paths), "checkpoint step exists")

    ckpt_paths = utils.get_checkpoint_iterator(
        [11, 19, 20], model_dir, find_closest=False)
    expected = [os.path.join(model_dir, "model.ckpt-20")]
    self.assertAllEqual(expected, list(ckpt_paths), "only step 20 exists")


if __name__ == "__main__":
  tf.test.main()
