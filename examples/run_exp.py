import sys
sys.path.append('../src/')
import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'

import numpy as np
from time import time, localtime, strftime
import configparser

from util.data import Dataset
from offlineExp.gru4rec import GRU4Rec
from offlineExp.tmf import TMF, TMF_variety, TMF_fast, TMF_fast_variety
from offlineExp.mf import MF, MF_v
from offlineExp.tf import TF
# from trainer.trainer import TARS_Trainer as Trainer
from trainer.trainer import OP_Trainer 
from trainer.trainer import OPPT_Trainer

from evaluator.evaluator import OP_Evaluator, OPPT_Evaluator

# np.random.seed(2020)

def _get_conf(conf_name):
    config = configparser.ConfigParser()
    config.read("../conf/"+conf_name+".properties")
    conf=dict(config.items("default"))
    conf['mode'] = conf['mode'].lower()
    return conf

def _logging_(basis_conf, params_conf):
    now = localtime(time())
    now = strftime("%Y-%m-%d %H:%M:%S", now)
    origin_data_name = basis_conf["data.input.dataset"]
    debiasing = basis_conf["debiasing"]
    print(now + " - data: %s" % origin_data_name)
    print(now + " - task: %s" % (params_conf['task']))
    print(now + " - model: %s, debiasing: %s" % (basis_conf['mode'], debiasing))
    # print(now + " - use gpu: %s" % (basis_conf['use_gpu']))
    if ("evaluation" in basis_conf) and (basis_conf['evaluation'].lower() == 'true'):
        print(now + " - directly load well-trained model and evaluate", flush=True)
    
    if basis_conf['mode'][0] != 'b': # baselines do not have params
        print("conf : " + str(params_conf), flush=True)
    

def run_dqn():
    conf = _get_conf('ml-100k')

    # init DQN
    config = load_parameters(conf['mode'])
    
    # tuning = 'learning_rate'.upper()
    # tuning = 'memory_size'.upper()
    # tuning = 'batch_size'.upper()
    # tuning = 'gamma'.upper()
    # tuning = 'optimizer'.upper()
    # tuning = 'replace_targetnet'.upper()
    # tuning = 'epsilon_decay_step'
    # tuning = 'lr_decay_step'
    # tuning = "state_encoder"
    # tuning = 'action_dim'.upper()
    # tuning = 'RNN_STATE_DIM'
    # print("tuning:",tuning)
    # config['SAVE_MODEL_FILE'] = conf["data.input.dataset"] + '_' + \
    #     conf["data.gen_model"] + '_' + conf["data.debiasing"] + '_' + \
    #     conf['mode'] + '_' + config["state_encoder"] + '_' + 'r01_SmoothL1_' + 'notrick_' + tuning + str(config[tuning]) + '_' 
    # config['SAVE_MODEL_FILE'] = 'sim_random_' + str(num_users) + '_' + str(action_space) + '_' + config["state_encoder"] + '_'

    task = 'OIPT'
    # task = 'OPPT'
    if 'task' in conf:
        task = conf['task']
    config['task'] = task
    _logging_(conf, config)
    ## loading data
    data = Dataset(conf, task=task)
    # ctr = data.train['ctr']
    
    # Super simple baselines just need some statistic info without training process.
    if 'b' in conf['mode']:
        if config['task'] == 'OIPT':
            evaluator = OP_Evaluator(None, None, data)
            # evaluator.evaluate(baselines=conf['mode'], subset='neg')
            for subset in [None, 'pos', 'neg']:
                evaluator.evaluate(baselines=conf['mode'], subset=subset)
            # for subset in [None, 'pos', 'neg']:
            #     for i in range(1, 4):
            #         print("\n*-*-*-*-*- B%d -*-*-*-*-*" % i)
            #         evaluator.evaluate(baselines='b%d'%i, subset=subset)
        else:
            evaluator = OPPT_Evaluator(None, None, data)
            evaluator.evaluate(baselines=conf['mode'])
        exit(0)

    # add some fixed parameters
    config['path'] = conf['data.input.path']
    config['dataset'] = conf['data.input.dataset']
    config['epochs'] = 500
    if conf['debiasing'].lower() == 'ips':
        config['debiasing'] = True
    else:
        config['debiasing'] = False
    
    if conf['mode'].lower() == "tmf":
        MODEL = TMF
    elif conf['mode'].lower() == "tmf_v":
        MODEL = TMF_variety
    elif conf['mode'].lower() == "tmf_fast":
        MODEL = TMF_fast
    elif conf['mode'].lower() == "tmf_fast_v":
        MODEL = TMF_fast_variety
    elif conf['mode'].lower() == "tf":
        MODEL = TF
    elif conf['mode'].lower() == "mf":
        MODEL = MF
    elif conf['mode'].lower() == "mf_v":
        MODEL = MF_v
    elif conf['mode'].lower() == "gru4rec":
        MODEL = GRU4Rec
    else:
        NotImplementedError("Make sure 'mode' in ['GRU4Rec', 'TMF', 'TMF_fast', 'MF', 'TF']!")

    # # add random-splitting for task 1: OIPT
    if task == 'OIPT':
        config['splitting'] = 'time'
        if 'splitting' in conf:
            config['splitting'] = conf['splitting']

    # # train process
    config['mode'] = conf['mode']
    model = MODEL(config, data, debiasing=config['debiasing'])
    if ('task' in config) and (config['task']=='OPPT'):
        Trainer = OPPT_Trainer
    else:
        Trainer = OP_Trainer
    trainer = Trainer(config, model, data)
    if ("evaluation" in conf) and (conf['evaluation'].lower() == 'true'):
        print("Directly load well-trained model and evaluate")
        trainer.load_model()
        print("saved params:", model.state_dict().keys())
    else:
        model = trainer.fit()

    # evaluate process
    model.eval()
    if ('task' in config) and (config['task']=='OPPT'):
        Evaluator = OPPT_Evaluator
    else:
        Evaluator = OP_Evaluator
    evaluator = Evaluator(config, model, data)
    evaluator.evaluate()
    # evaluator.evaluate(ub='false')
    # evaluator.evaluate(ub='snips')
    # for thr in [1e-2, 1e-3, 1e-4]:
    #     evaluator.evaluate(ub='pop', threshold=thr)
    #     evaluator.evaluate(ub='unpop', threshold=thr)
    

def load_parameters(mode):
    params = {}
    config = configparser.ConfigParser()
    if 'tmf_fast_v' in mode.lower():
        mode = 'tmf_fast'
    elif 'tmf_v' in mode.lower():
        mode = 'tmf'
    elif 'mf_v' in mode.lower():
        mode = 'mf'
    elif mode[0] == 'b':
        return {}
    config.read("../conf/"+mode+".properties")
    conf=dict(config.items("hyperparameters"))
    # for multiple jobs in 
    args = set_hparams()
    if args.lr is not None:
        conf["learning_rate"] = args.lr
    if args.reg is not None:
        conf['l2_reg'] = args.reg
    return conf

def set_hparams():
    import argparse
    parser = argparse.ArgumentParser()
    # parser.add_argument('--seed', type=int)
    parser.add_argument('--lr', type=float, default=None)
    parser.add_argument('--reg', type=float, default=None)
    args = parser.parse_args()
    print("now lr is", args.lr, ", and reg is", args.reg, flush=True)
    # np.random.seed(args.seed)
    return args

if __name__ == "__main__":
    run_dqn()
    print("End. " + strftime("%Y-%m-%d %H:%M:%S", localtime(time())))
# print("checkpoint")