# -*- coding: utf-8 -*-

# This code is part of Qiskit.
#
# (C) Copyright IBM 2018, 2019.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

""" Test Caching """

import unittest
import pickle
import tempfile
import os
from test.aqua.common import QiskitAquaTestCase

import numpy as np
from parameterized import parameterized
from qiskit import BasicAer
from qiskit.qobj import Qobj

from qiskit.aqua import QuantumInstance, QiskitAqua, aqua_globals
from qiskit.aqua.input import EnergyInput
from qiskit.aqua.components.variational_forms import RY, RYRZ
from qiskit.aqua.components.optimizers import L_BFGS_B
from qiskit.aqua.components.initial_states import Zero
from qiskit.aqua.algorithms.adaptive import VQE
from qiskit.aqua.utils import CircuitCache
from qiskit.aqua.operators import WeightedPauliOperator


class TestCaching(QiskitAquaTestCase):
    """ Test Caching """
    def setUp(self):
        super().setUp()
        self.seed = 50
        aqua_globals.random_seed = self.seed
        pauli_dict = {
            'paulis': [{"coeff": {"imag": 0.0, "real": -1.052373245772859}, "label": "II"},
                       {"coeff": {"imag": 0.0, "real": 0.39793742484318045}, "label": "IZ"},
                       {"coeff": {"imag": 0.0, "real": -0.39793742484318045}, "label": "ZI"},
                       {"coeff": {"imag": 0.0, "real": -0.01128010425623538}, "label": "ZZ"},
                       {"coeff": {"imag": 0.0, "real": 0.18093119978423156}, "label": "XX"}
                       ]
        }
        qubit_op = WeightedPauliOperator.from_dict(pauli_dict)
        self.algo_input = EnergyInput(qubit_op)
        self.reference_vqe_result = None

    def _build_refrence_result(self, backends):
        res = {}
        os.environ.pop('QISKIT_AQUA_CIRCUIT_CACHE', None)
        for backend in backends:
            params_no_caching = {
                'algorithm': {'name': 'VQE'},
                'problem': {'name': 'energy',
                            'random_seed': self.seed,
                            'circuit_caching': False,
                            'skip_qobj_deepcopy': False,
                            'skip_qobj_validation': False,
                            'circuit_cache_file': None,
                            },
                'backend': {'provider': 'qiskit.BasicAer', 'name': backend},
            }
            if backend != 'statevector_simulator':
                params_no_caching['backend']['shots'] = 1000
                params_no_caching['optimizer'] = {'name': 'SPSA', 'max_trials': 15}
            qiskit_aqua = QiskitAqua(params_no_caching, self.algo_input)
            res[backend] = qiskit_aqua.run()
        os.environ['QISKIT_AQUA_CIRCUIT_CACHE'] = '1'
        self.reference_vqe_result = res

    @parameterized.expand([
        ['statevector_simulator', True, True],
        ['qasm_simulator', True, True],
        ['statevector_simulator', True, False],
        ['qasm_simulator', True, False],
    ])
    def test_vqe_caching_via_run_algorithm(self, backend, caching, skip_qobj_deepcopy):
        """ VQE Caching Via Run Algorithm test """
        self._build_refrence_result(backends=[backend])
        skip_validation = True
        params_caching = {
            'algorithm': {'name': 'VQE'},
            'problem': {'name': 'energy',
                        'random_seed': self.seed,
                        'circuit_caching': caching,
                        'skip_qobj_deepcopy': skip_qobj_deepcopy,
                        'skip_qobj_validation': skip_validation,
                        'circuit_cache_file': None,
                        },
            'backend': {'provider': 'qiskit.BasicAer', 'name': backend},
        }
        if backend != 'statevector_simulator':
            params_caching['backend']['shots'] = 1000
            params_caching['optimizer'] = {'name': 'SPSA', 'max_trials': 15}
        qiskit_aqua = QiskitAqua(params_caching, self.algo_input)
        result_caching = qiskit_aqua.run()

        self.assertAlmostEqual(result_caching['energy'],
                               self.reference_vqe_result[backend]['energy'])

        np.testing.assert_array_almost_equal(self.reference_vqe_result[backend]['eigvals'],
                                             result_caching['eigvals'], 5)
        np.testing.assert_array_almost_equal(self.reference_vqe_result[backend]['opt_params'],
                                             result_caching['opt_params'], 5)
        if qiskit_aqua.quantum_instance.has_circuit_caching:
            self.assertEqual(qiskit_aqua.quantum_instance._circuit_cache.misses, 0)
        self.assertIn('eval_count', result_caching)
        self.assertIn('eval_time', result_caching)

    @parameterized.expand([
        [4],
        [1]
    ])
    def test_vqe_caching_direct(self, max_evals_grouped):
        """ VQE Caching Direct test """
        self._build_refrence_result(backends=['statevector_simulator'])
        backend = BasicAer.get_backend('statevector_simulator')
        num_qubits = self.algo_input.qubit_op.num_qubits
        init_state = Zero(num_qubits)
        var_form = RY(num_qubits, 3, initial_state=init_state)
        optimizer = L_BFGS_B()
        algo = VQE(self.algo_input.qubit_op,
                   var_form,
                   optimizer,
                   max_evals_grouped=max_evals_grouped)
        quantum_instance_caching = QuantumInstance(backend,
                                                   circuit_caching=True,
                                                   skip_qobj_deepcopy=True,
                                                   skip_qobj_validation=True,
                                                   seed_simulator=self.seed,
                                                   seed_transpiler=self.seed)
        result_caching = algo.run(quantum_instance_caching)
        self.assertLessEqual(quantum_instance_caching.circuit_cache.misses, 0)
        self.assertAlmostEqual(self.reference_vqe_result['statevector_simulator']['energy'],
                               result_caching['energy'])
        speedup_min = 3
        speedup = result_caching['eval_time'] / \
            self.reference_vqe_result['statevector_simulator']['eval_time']
        self.assertLess(speedup, speedup_min)

    def test_saving_and_loading_e2e(self):
        """ Saving And Loading e to e test """
        backend = BasicAer.get_backend('statevector_simulator')
        num_qubits = self.algo_input.qubit_op.num_qubits
        init_state = Zero(num_qubits)
        var_form = RY(num_qubits, 1, initial_state=init_state)
        optimizer = L_BFGS_B(maxiter=10)
        algo = VQE(self.algo_input.qubit_op, var_form, optimizer)

        with tempfile.NamedTemporaryFile(suffix='.inp', delete=True) as cache_tmp_file:
            cache_tmp_file_name = cache_tmp_file.name
            quantum_instance_caching = QuantumInstance(backend,
                                                       circuit_caching=True,
                                                       cache_file=cache_tmp_file_name,
                                                       skip_qobj_deepcopy=True,
                                                       skip_qobj_validation=True,
                                                       seed_simulator=self.seed,
                                                       seed_transpiler=self.seed)
            algo.run(quantum_instance_caching)
            self.assertLessEqual(quantum_instance_caching.circuit_cache.misses, 0)

            is_file_exist = os.path.exists(cache_tmp_file_name)
            self.assertTrue(is_file_exist, "Does not store content successfully.")

            circuit_cache_new = CircuitCache(skip_qobj_deepcopy=True,
                                             cache_file=cache_tmp_file_name)
            self.assertEqual(quantum_instance_caching.circuit_cache.mappings,
                             circuit_cache_new.mappings)
            self.assertLessEqual(circuit_cache_new.misses, 0)

    def test_saving_and_loading_one_circ(self):
        """ Saving and Loading one Circ test """
        with tempfile.NamedTemporaryFile(suffix='.inp', delete=True) as cache_tmp_file:
            cache_tmp_file_name = cache_tmp_file.name
            var_form = RYRZ(num_qubits=4, depth=5)
            backend = BasicAer.get_backend('statevector_simulator')

            params0 = aqua_globals.random.random_sample(var_form.num_parameters)
            circ0 = var_form.construct_circuit(params0)

            qi0 = QuantumInstance(backend,
                                  circuit_caching=True,
                                  cache_file=cache_tmp_file_name,
                                  skip_qobj_deepcopy=True,
                                  skip_qobj_validation=True,
                                  seed_simulator=self.seed,
                                  seed_transpiler=self.seed)

            _ = qi0.execute([circ0])
            with open(cache_tmp_file_name, "rb") as cache_handler:
                saved_cache = pickle.load(cache_handler, encoding="ASCII")
            self.assertIn('qobjs', saved_cache)
            self.assertIn('mappings', saved_cache)
            qobjs = [Qobj.from_dict(qob) for qob in saved_cache['qobjs']]
            self.assertTrue(isinstance(qobjs[0], Qobj))
            self.assertGreaterEqual(len(saved_cache['mappings'][0][0]), 50)

            qi1 = QuantumInstance(backend,
                                  circuit_caching=True,
                                  cache_file=cache_tmp_file_name,
                                  skip_qobj_deepcopy=True,
                                  skip_qobj_validation=True,
                                  seed_simulator=self.seed,
                                  seed_transpiler=self.seed)

            params1 = aqua_globals.random.random_sample(var_form.num_parameters)
            circ1 = var_form.construct_circuit(params1)

            qobj1 = qi1.circuit_cache.load_qobj_from_cache([circ1],
                                                           0,
                                                           run_config=qi1.run_config)
            self.assertTrue(isinstance(qobj1, Qobj))
            _ = qi1.execute([circ1])

            self.assertEqual(qi0.circuit_cache.mappings, qi1.circuit_cache.mappings)
            self.assertLessEqual(qi1.circuit_cache.misses, 0)


if __name__ == '__main__':
    unittest.main()
