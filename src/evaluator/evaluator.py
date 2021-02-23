import numpy as np
import pandas as pd
import torch

# metric definition
# target position starts from 1 to m.
def hit_(target_pos, K):
    return (target_pos <= K)
def mrr_(target_pos, K):
    mrr = 1.0 / target_pos
    mrr = np.where(target_pos <= K, mrr, 0.0)
    return mrr
def ndcg_(target_pos, K):
    # for only one correct example, (1 / log2(posi+1)) / (1 / log2(1+1)) = log2(2) / log2(posi+1)
    result = np.log(2.) / np.log(1.0 + target_pos)
    result = np.where(target_pos <= K, result, 0.0)
    return result


def calculate_metrics(target_pos, weight=None, normalization='snips', threshold=1e-3):
    results = {}
    K = 10
    if weight is None:
        print("evaluation on standard metrics")
        results['mrr@'+str(K)] = round(mrr_(target_pos, K).mean(), 8)
        results['hit@'+str(K)] = round(hit_(target_pos, K).mean(), 8)
        results['ndcg@'+str(K)] = round(ndcg_(target_pos, K).mean(), 8)
    elif normalization.lower() == "snips":
        print("evaluation on snips metrics")
        weight_inverse = np.reciprocal(weight)
        weight_ = weight_inverse / weight_inverse.sum() # normalization based on users, since one target per user
        results['mrr@'+str(K)] = round((mrr_(target_pos, K) * weight_).sum(), 8)
        results['hit@'+str(K)] = round((hit_(target_pos, K) * weight_).sum(), 8)
        results['ndcg@'+str(K)] = round((ndcg_(target_pos, K) * weight_).sum(), 8)
    elif normalization == 'pop':
        # threshold = 1.0e-3
        condition = (weight > threshold)
        print("evaluation on pop items with ctr bigger than", threshold, ", #ui_pairs", condition.sum())
        results['mrr@'+str(K)] = round((mrr_(target_pos, K)[condition]).mean(), 3)
        results['hit@'+str(K)] = round((hit_(target_pos, K)[condition]).mean(), 3)
        results['ndcg@'+str(K)] = round((ndcg_(target_pos, K)[condition]).mean(), 3)
    elif normalization == 'unpop':
        # threshold = 1.0e-3
        condition = (weight < threshold)
        print("evaluation on un-pop items with ctr smaller than", threshold, ", #ui_pairs", condition.sum())
        results['mrr@'+str(K)] = round((mrr_(target_pos, K)[condition]).mean(), 3)
        results['hit@'+str(K)] = round((hit_(target_pos, K)[condition]).mean(), 3)
        results['ndcg@'+str(K)] = round((ndcg_(target_pos, K)[condition]).mean(), 3)

    else:
        raise NotImplementedError
    return results

def _mse(pred, true):
    return np.mean((true - pred)**2)
def _mae(pred, true):
    return np.absolute(true - pred).mean()
def _acc(pred, true):
    if (min(true) == 0.0) and (max(true) == 1.0):        
        pred = np.round(pred).astype(int)
    return (pred == true).mean()

def cal_ratpred_metrics(score, target):
    # for ml-100K, ratings are 1, 2, 3, 4, 5
    score = score.clip(min(target), max(target))
    score_ = np.round(score).astype(int)
    
    # # since in target, 0.5, 1.0, 1.5, ..., 4.5, 5.0
    # target = target * 2 # 1, 2, 3, ..., 9, 10
    # score = score * 2
    # score = score.clip(min(target), max(target))
    # score_ = np.round(score).astype(int) * 1.0 / 2
    # score /= 2.0
    # target /= 2.0

    results = {}
    results['mse'] = _mse(score, target)
    results['mae'] = _mae(score, target)
    results['acc'] = _acc(score_, target)
    return results

def _nll(score, true):
    nll_score = true * np.log(score) + (1.0-true) * np.log(1-score) 
    return - nll_score.mean()
def _perplexity(score, true):
    nll_score = true * score * np.log2(score) + (1.0-true) * (1-score) * np.log2(1-score) 
    # nll_score = true * np.log2(score) + (1.0-true) * np.log2(1.0-score)
    return np.power(2, -nll_score.mean())
def cal_op_metrics(score, target, w_sigmoid=True):
    # score_ = score.copy()
    if w_sigmoid:
        score = 1.0 / (1.0 + np.exp(-score)) # sigmoid(score)
    score = score.clip(0.000001, 0.999999)
    # if (score == 1.0).sum() + (score == 0.0).sum() > 0:
    #     print(score_[score == 1.0], score_[score==0.0])
    #     exit(1)
    results = {}
    results['acc'] = _acc(score, target)
    results['nll'] = _nll(score, target)
    results['ppl'] = _perplexity(score, target)
    return results

class AbstractEvaluator(object):
    """:class:`AbstractEvaluator` is an abstract object which supports
    the evaluation of the model. It is called by :class:`Trainer`.
    Note:       
        If you want to inherit this class and implement your own evalautor class, 
        you must implement the following functions.
    Args:
        config (Config): The config of evaluator.
    """

    def __init__(self, config, model, data):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if model is not None:
            self.model = model.to(self.device)
        self.config = config
        self.data = data

    def _check_args(self):
        """check the correct of the setting"""
        raise NotImplementedError

    def collect(self):
        """get the intermediate results for each batch, it is called at the end of each batch"""
        raise NotImplementedError

    def evaluate(self):
        """calculate the metrics of all batches, it is called at the end of each epoch"""
        raise NotImplementedError

    def metrics_info(self):
        """get metrics result"""
        raise NotImplementedError

    def _calculate_metrics(self):
        """ to calculate the metrics"""
        raise NotImplementedError

class Evaluator(AbstractEvaluator):
    def __init__(self, config, model, data):
        super(Evaluator, self).__init__(config, model, data)
        self.max_item_list_len = 20 # config['max_item_list_len']

    def _data_pre_fullseq(self, train_full, test):
        train = train_full
        n_items = self.data.n_items
        uid_list, seq_list, seq_len, ctr_target, target = [], [], [], [], []
        u_target = dict(zip(test['UserId'], test['ItemId']))
        u_ctr = dict(zip(test['UserId'], test['ctr']))
        for u, group in train.groupby(['UserId']):
            u_ratings = group.sort_values(['timestamp'], ascending=True)
            u_items = u_ratings['ItemId'].values
            u_n_items = len(u_items)
            target.append(u_target[u])
            ctr_target.append(u_ctr[u])
            uid_list.append(u)
            if u_n_items >= self.max_item_list_len:
                seq_list.append(u_items[-self.max_item_list_len:])
                seq_len.append(self.max_item_list_len)
            else:
                padding_list = [n_items] * (self.max_item_list_len - u_n_items)
                seq_list.append(list(u_items) + padding_list)
                seq_len.append(u_n_items)
        uid_list = torch.from_numpy(np.array(uid_list, dtype=int)).to(self.device)
        seq_list = torch.from_numpy(np.array(seq_list, dtype=int)).to(self.device)
        seq_len = torch.from_numpy(np.array(seq_len, dtype=int)).to(self.device)
        target = torch.from_numpy(np.array(target, dtype=int)).to(self.device)
        # ctr_target = torch.from_numpy(np.array(ctr_target, dtype=float)).to(self.device)
        ctr_target = np.array(ctr_target, dtype=float)
        interaction = {}
        interaction['seq'] = seq_list
        interaction['seq_len'] = seq_len
        interaction['target'] = target
        # interaction['ctr'] = ctr_target
        assert seq_list.size()[0] == seq_len.size()[0]
        interaction['num'] = seq_list.size()[0]
        return interaction, ctr_target
    
    def evaluate(self, ub='false', threshold=1e-3):
        ''' ub: unbiased evaluator'''
        interaction, ctr_target = self._data_pre_fullseq(self.data.train_full, self.data.test)
        # results
        scores = self.model.full_sort_predict(interaction)
        targets = interaction['target']
        target_scores = torch.gather(scores, 1, targets.view(-1, 1)) # [B 1]
        target_pos = (scores >= target_scores).sum(-1) # [B]
        if ub.lower() in ['snips', 'pop', 'unpop']:
            results = calculate_metrics(target_pos.cpu().numpy(), weight=ctr_target, normalization=ub, threshold=threshold)
        else:
            results = calculate_metrics(target_pos.cpu().numpy())
        print("results on testset: %s" % (str(results)))
        return results
    
    # def metrics_info(self, results):
    
class RatPred_Evaluator(AbstractEvaluator):
    def __init__(self, config, model, data):
        super(RatPred_Evaluator, self).__init__(config, model, data)

    def _data_pre(self, test):
        uid_list = torch.from_numpy(np.array(test['UserId'].values, dtype=int)).to(self.device)
        iid_list = torch.from_numpy(np.array(test['ItemId'].values, dtype=int)).to(self.device)
        target = torch.from_numpy(np.array(test['rating'].values, dtype=float)).to(self.device)
        itemage = torch.from_numpy(np.array(test['ItemAge'].values, dtype=int)).to(self.device)
        
        interaction = {}
        interaction['user'] = uid_list
        interaction['item'] = iid_list
        interaction['target'] = target
        interaction['itemage'] = itemage

        interaction['num'] = uid_list.size()[0]
        return interaction
    
    @torch.no_grad() 
    def evaluate(self, ub='false', threshold=1e-3):
        ''' ub: unbiased evaluator'''
        interaction = self._data_pre(self.data.test)
        # results
        scores = self.model.predict(interaction)
        targets = interaction['target']
        results = cal_ratpred_metrics(scores.cpu().numpy(), targets.cpu().numpy())
        print("results on testset: %s" % (str(results)))
        return results
    
    # def metrics_info(self, results):
        

class OP_Evaluator(AbstractEvaluator):
    def __init__(self, config, model, data):
        super(OP_Evaluator, self).__init__(config, model, data)
        self.n_items = self.data.n_items
        self.n_users = self.data.n_users
        self.n_periods = self.data.n_periods
        self.item_birthdate = torch.from_numpy(self.data._get_item_birthdate()).to(self.device)
        
        # get last timestamp of user in the system from the training set, and evaluate on the next one-month
        self.user_lasttime_train = self._get_trainU_last()
        self.test_user_pos = {} # filled when we call _data_pre_next_month()
        self.period_type = self.data.period_type
        if self.period_type == 'month':
            self.period_s = 30 * 24 * 60 * 60
        elif self.period_type == 'year':
            self.period_s = 365 * 24 * 60 * 60
        # torch.manual_seed(517)
        # np.random.seed(517)

    def _get_trainU_last(self):
        train = self.data.train_full
        user_lasttime_train = np.zeros(self.n_users)
        for u, group in train.groupby(by=['UserId']):
            lasttime = max(group['timestamp'])
            user_lasttime_train[u] = lasttime
        return user_lasttime_train
    
    # only keep the next month/year interactions for every user as test set
    def _filter_test_next_month(self):
        test = self.data.test
        user_lasttime_test = self.user_lasttime_train + self.period_s
        lasttime_test = user_lasttime_test[test['UserId']]
        n_test = len(test)
        test = test[test['timestamp'] <= lasttime_test]
        print("Filter %d interactions which do not happend in next month since last interaction in training, \
            in total %d ratings observed in testset" % (n_test - len(test), len(test)))
        return test

    def _data_pre_next_month(self):
        test = self._filter_test_next_month()
        uid_list = torch.from_numpy(np.array(test['UserId'].values, dtype=int)).to(self.device)
        iid_list = torch.from_numpy(np.array(test['ItemId'].values, dtype=int)).to(self.device)
        target = torch.ones_like(iid_list).to(self.device)
        itemage = torch.from_numpy(np.array(test['ItemAge'].values, dtype=int)).to(self.device)
        timestamp = torch.from_numpy(np.array(test['timestamp'].values, dtype=int)).to(self.device)

        interaction = {}
        interaction['user'] = uid_list
        interaction['item'] = iid_list
        interaction['target'] = target
        interaction['itemage'] = itemage
        interaction['timestamp'] = timestamp
        interaction['num'] = uid_list.size()[0]

        for u, group in test.groupby(by=['UserId']):
            self.test_user_pos[u] = group['ItemId'].values
            
        return interaction

    def _neg_sampling_next_month(self, K=1, full_negs = True):
        # filter the data and keep one-month interactions
        test = self._filter_test_next_month()
        
        # period_s = 15 * 24 * 60 * 60 # for the negatives, we use the middle date of next one month/year (period) as timestamp
        user_time_negs = self.user_lasttime_train + self.period_s / 2.0
        # negatives
        users, items = [], []
        for u, group in test.groupby(by=['UserId']):
            pos_items = group['ItemId'].values
            neg_prob = np.random.uniform(0, 1, size=(self.n_items,))
            neg_prob[pos_items] = -1.
            # delete the items published after user last interaction in training set
            neg_prob[self.data.item_birthdate >= self.user_lasttime_train[u]] = -1.
            if full_negs:
                neg_items = np.arange(self.n_items)[neg_prob >= 0.0]
            else:
                neg_items = np.argsort(neg_prob)[-len(pos_items) : ]
            users.append(np.repeat(u, len(neg_items)))
            items.append(neg_items)
        users = np.concatenate(users, axis=0)
        items = np.concatenate(items, axis=0)
        timestamps = user_time_negs[users]
        # print(timestamps.astype('datetime64[s]'))
        # print(items)
        # print(self.data.item_birthdate[items].astype('datetime64[s]'))
        itemage_neg = self.data.get_itemage(items, timestamps)

        # print("******** Fake test, using +5 month **********")
        # itemage_neg = np.clip(itemage_neg+5, 0, self.data.n_periods - 1)
        itemage = self.data.get_itemage(test['ItemId'].values, test['timestamp'].values)
        assert len(users) == len(items)
        assert len(users) == len(itemage_neg)

        n_pos, n_neg = len(test), len(users)
        print("#pos: %d, #neg: %d" % (n_pos, n_neg))
        users = np.concatenate((test['UserId'].values, users)).astype(int)
        uid_list = torch.from_numpy(users).to(self.device)
        items = np.concatenate((test['ItemId'].values, items)).astype(int)
        iid_list = torch.from_numpy(items).to(self.device)
        target = torch.cat((torch.ones((n_pos, )), torch.zeros((n_neg, ))), axis=0).to(self.device)
        itemage = self.data.get_itemage(test['ItemId'].values, test['timestamp'].values)
        itemages = np.concatenate((itemage, itemage_neg), axis=0)
        itemages = torch.from_numpy(itemages).to(self.device)

        interaction = {}
        interaction['user'] = uid_list
        interaction['item'] = iid_list
        interaction['target'] = target
        interaction['itemage'] = itemages
        interaction['num'] = n_pos + n_neg
        return interaction

    def _data_pre(self, test):
        uid_list = torch.from_numpy(np.array(test['UserId'].values, dtype=int)).to(self.device)
        iid_list = torch.from_numpy(np.array(test['ItemId'].values, dtype=int)).to(self.device)
        target = torch.ones_like(iid_list).to(self.device)
        itemage = torch.from_numpy(np.array(test['ItemAge'].values, dtype=int)).to(self.device)
        timestamp = torch.from_numpy(np.array(test['timestamp'].values, dtype=int)).to(self.device)

        interaction = {}
        interaction['user'] = uid_list
        interaction['item'] = iid_list
        interaction['target'] = target
        interaction['itemage'] = itemage
        interaction['timestamp'] = timestamp
        interaction['num'] = uid_list.size()[0]
        return interaction

    def _neg_sampling(self, interaction):
        interaction_ = {}
        users = interaction['user']
        items = interaction['item']
        targets = interaction['target']
        timestamp = interaction['timestamp']
        itemage = interaction['itemage']

        # generate data for negs
        interaction_['user'] = torch.cat((users, users), 0)
        negs = torch.randint(self.n_items, size=(interaction['num'],)).to(self.device)
        interaction_['item'] = torch.cat((items, negs), 0)
        target_neg = (items == negs).int()
        interaction_['target'] = torch.cat((targets, target_neg), 0)
        itemage_neg = self.data.get_itemage(items, timestamp, self.item_birthdate)
        # itemage_neg = ((timestamp - self.item_birthdate[items]) * 1.0 / (30*24*60*60)).int().clip(0, self.data.n_months - 1) # unit: month
        interaction_['itemage'] = torch.cat((itemage, itemage_neg), 0)
        interaction_['num'] = interaction['num'] * 2
        return interaction_
    
    def _neg_sampling_timebased(self, interaction):
        interaction_ = {}
        users = interaction['user']
        items = interaction['item']
        targets = interaction['target']
        timestamp = interaction['timestamp']
        itemage = interaction['itemage']

        # generate data for negs
        interaction_['user'] = torch.cat((users, users), 0)
        negs = torch.randint(self.n_items, size=(interaction['num'],)).to(self.device)
        interaction_['item'] = torch.cat((items, negs), 0)
        target_neg = (items == negs).int()
        interaction_['target'] = torch.cat((targets, target_neg), 0)
        itemage_neg = self.data.get_itemage(items, timestamp, self.item_birthdate)
        # itemage_neg = ((timestamp - self.item_birthdate[items]) * 1.0 / (30*24*60*60)).int().clip(0, self.data.n_months - 1) # unit: month
        interaction_['itemage'] = torch.cat((itemage, itemage_neg), 0)
        interaction_['num'] = interaction['num'] * 2
        return interaction_

    def _data_random(self, num = 1000):
        users = torch.randint(self.data.n_users, size=(num, )).to(self.device)
        items = torch.randint(self.n_items, size=(num, )).to(self.device)
        itemage =torch.randint(self.n_periods, size=(num, )).to(self.device)
        target = torch.where(itemage >= 0.5 * self.n_periods, 0, 1).to(self.device)
        return {'user':users, 'item':items, 'target':target, 'itemage':itemage, 'num':num}

    @torch.no_grad()
    def _eval_epoch(self, interaction, batch_size=512):
        users = interaction['user']
        items = interaction['item']
        # targets = interaction['target']
        itemages = interaction['itemage']
        
        scores = []
        num_batch = int(np.ceil(interaction['num'] * 1.0 / batch_size))
        for i_batch in range(num_batch):
            start_idx = i_batch * batch_size
            end_idx = (i_batch+1) * batch_size
            score = self.model.predict({'user':users[start_idx:end_idx], \
                'item':items[start_idx:end_idx], 'itemage':itemages[start_idx:end_idx]})
            scores.append(score)
        return torch.cat(scores, 0)
        
    @torch.no_grad() 
    def evaluate(self, ub='false', threshold=1e-3, baselines = None, subset = None):
        self._save_pred_OP()

        torch.manual_seed(517)
        np.random.seed(517)
        # ''' ub: unbiased evaluator'''
        # interaction_pro = self._data_pre(self.data.test)
        # interaction = interaction_pro
        # interaction = self._neg_sampling(interaction_pro)
        # # randomly generate the data to verify if time affects ?
        # interaction = self._data_random()
        
        # evaluate on testset with the interactions happening among next one-month per user
        interaction = self._neg_sampling_next_month(full_negs=True)
        # self._save_something(interaction)
        # exit(0)
        w_sigmoid = False

        # # results
        if baselines is not None:
            # # simple baselines
            assert baselines[0] == 'b'
            scores, w_sigmoid, interaction = self.baselines(interaction, variety=int(baselines[1]), subset=subset) # w_sigmoid means if we should do sigmoid later in cal_op_metrics.
        else:
            scores = self._eval_epoch(interaction)
            scores /= 3.5
            print("The chance to generate 1 is %.6f" % ((scores>0.5).sum()*1.0 / len(scores)))
        
        # print("the max score of the probability is ", torch.max(scores))
        # # evaluate it as a prediction task, but too many 0s
        print("results on testset:")
        targets = interaction['target']
        results = cal_op_metrics(scores.cpu().numpy(), targets.cpu().numpy(), w_sigmoid=w_sigmoid)
        print('\t'.join(results.keys()), '\n', '\t'.join([str(x) for x in results.values()]))
        # evaluate it as a ranking task
        results = cal_ob_pred2ranking_metrics(interaction, scores)
        print('\t'.join(results.keys()), '\n', '\t'.join([str(x) for x in results.values()]))
        # print("results on testset: %s" % (str(results)))
        return results
    
    def _save_something(self, interaction):
        # # save itemage_obLabels
        # targets = interaction['target'].cpu().numpy()
        # itemages = interaction['itemage'].cpu().numpy()
        # df = pd.DataFrame(data={'target':targets, 'itemage':itemages})
        # df.to_csv('itemage_obLabels.csv', index=False)
        
        # # save the distribution of predicted scores sigmoid(W * T + b)
        itemages = torch.arange(self.n_periods).to(self.device)
        scores = self.model.forward(None, None, itemages)
        print(','.join([str(x) for x in scores.cpu().numpy()]))

        # # save the distribution of the p_T in testset
        # targets = interaction['target'].cpu().numpy()
        # itemages = interaction['itemage'].cpu().numpy()
        # p_T_test = []
        # for i in range(self.n_periods):
        #     idx = itemages == i
        #     p_T_test.append(targets[idx].mean())
        # print(','.join([str(x) for x in p_T_test]))
        # print(','.join([str(int((itemages == i).sum())) for i in range(self.n_periods)]))

    def _save_pred_OP(self):
        data = pd.concat([self.data.train_full, self.data.test], ignore_index=True)
        interaction = self._data_pre(data)
        scores = self._eval_epoch(interaction).cpu().numpy()
        scores /= 3.5
        assert len(scores) == len(data)
        data['predOP'] = scores

        print("--------- Save the predicted Observation Probabilities of all observed (u,i,t) ----------")
        path = self.config['path'] + self.config['dataset'] + '/predOP_' + self.config['mode'] + '.csv'
        data.to_csv(path, sep=',', header=True, index=False)
        
    
    def baselines(self, interaction, variety=1, subset = None): # we should use the training set rather than test set.
        '''some simple baseline variety
        B1: all scores equal to 0.0
        B2: all scores equal to a fixed value, 0.005 or 0.00478 best
        B3: scores for (u, i) at T equal to a fixed value (diff at diff T).  * Norm, 0.1 / 0.013641 best.
        B4: scores for given (i, T) fixed.
        --- Params ---
        subset: the evaluation subset, if None we use all pos+negs, if pos we only use pos, else only negs
        '''
        # train only contains positives
        # train = self.data.train_full
        train = self.data.train
        w_sigmoid = False # do not do sigmoid later in cal_op_metrics
        targets = interaction['target']
        if subset is not None:
            if subset == 'pos':
                print(" ------ We only evaluate on the positives ----- ")
                idx = targets == 1
            else:
                print(" ------ We only evaluate on the negatives ----- ")
                idx = targets == 0
            targets = targets[idx]
            interaction_ = {'num':idx.sum(), 'target':targets}
            for key in interaction.keys():
                if key not in ['num', 'target']:
                    interaction_[key] = interaction[key][idx]
            interaction = interaction_
        scores = torch.zeros_like(targets).float().to(self.device)
        if variety == 1:
            print("****** Note we want to know what happened if we give all the predicted scores %.6f" % (scores[0].cpu()))
        elif variety == 2:
            # scores += 0.004
            scores += targets.cpu().numpy().mean()
            # scores += len(train) * 1.0 / (self.n_users * self.n_items  * self.n_periods)
            print("****** Note we want to know what happened if we give all the predicted scores %.6f" % (scores[0].cpu()))
        elif variety == 3:
            scores_T = []
            itemages = interaction['itemage']
            num_D = self.n_users * self.n_items
            norm = 2 #0.1 / 0.013641
            print("We do normalization: %f" % norm)
            for T in range(self.n_periods):
                # # calculate in training set
                s_T = (train['ItemAge'] == T).sum() * 1. / num_D * norm
                # # calculate in test set
                # s_T = targets[itemages == T].mean()
                scores[itemages == T] = s_T
                scores_T.append(s_T)
            print("****** Note we want to know what happened if we give all the predicted scores %s" % str(scores_T))
        elif variety == 4:
            itemages = interaction['itemage']
            items = interaction['item']
            num_D = self.n_users * self.n_items
            s_iT = train.groupby(['ItemId', 'ItemAge']).size() * 1. / num_D
            norm = 0.012 / s_iT.mean()
            print("We do normalization: %f" % norm)
            s_iT *= norm
            for i, T in s_iT.index:
                scores[(items == i) & (itemages == T)] = s_iT[i, T]
        return scores, w_sigmoid, interaction

    # def metrics_info(self, results):

def cal_ob_pred2ranking_metrics(interaction, scores, K=10):
    users, items = interaction['user'].cpu().numpy(), interaction['item'].cpu().numpy()
    targets = interaction['target'].cpu().numpy()
    scores = scores.cpu().numpy()
    df = pd.DataFrame(data = {'user': users, 'item':items, 'target':targets, 'score':scores})
    Prec, Recall, MRR, MAP, NDCG = [], [], [], [], []
    for u, group in df.groupby(['user']):
        # for now poss before the negs, so we need shuffle first
        shuffle_order = np.random.permutation(len(group))
        group_shuffled = group.iloc[shuffle_order]
        group_sorted = group_shuffled.sort_values(by=['score'], ascending=False)
        '''NDCG...MAP...'''
        preds = group_sorted['target'][:K]
        Prec.append(preds.sum() * 1.0 / len(preds))
        if group_sorted['target'].sum() == 0:
            Recall.append(0.0)
        else:
            Recall.append(preds.sum() * 1.0 / group_sorted['target'].sum())
        MRR.append(mrrs_(preds))
        MAP.append(maps_(preds))
        NDCG.append(ndcgs_(preds))
    results = {}
    results['Prec@%d'%K] = (np.array(Prec)).mean()
    results['Recall@%d'%K] = (np.array(Recall)).mean()
    results['MRR@%d'%K] = (np.array(MRR)).mean()
    results['MAP@%d'%K] = (np.array(MAP)).mean()
    results['NDCG@%d'%K] = (np.array(NDCG)).mean()
    return results


# preds: [0 1 0 1] the targets sorted by predicted scores

def mrrs_(preds):
    if preds.sum() == 0:
        return 0.0
    position = np.where(preds>0)[0][0] + 1
    return 1.0 / position

def maps_(preds):
    if preds.sum() == 0:
        return 0.0
    positions = np.where(preds>0)[0]
    MAP = np.reciprocal(positions.astype(float)+1).sum() / len(preds)
    return MAP

# denominator
def ndcgs_(preds, denominator=None):
    if preds.sum() == 0:
        return 0.0
    if denominator is None:
        denominator = np.log2(np.arange(len(preds)).astype(float)+2) # i=2,...,k+1
    dcgs = ((np.power(2, preds).astype(float) - 1.0) / denominator).sum()
    pos_num = (preds > 0).sum()
    idcgs = (1.0 / denominator[:pos_num]).sum()
    return dcgs / idcgs


class OPPT_Evaluator(OP_Evaluator):
    def __init__(self, config, model, data):
        super(OPPT_Evaluator, self).__init__(config, model, data)
    
    def _data_pre_next_month(self):
        self.test = self._filter_test_next_month()
        test = self.test
        uid_list = torch.from_numpy(np.array(test['UserId'].values, dtype=int)).to(self.device)
        iid_list = torch.from_numpy(np.array(test['ItemId'].values, dtype=int)).to(self.device)
        # target = torch.from_numpy(np.where(test['rating'].values > 3, 1, 0).astype(int)).to(self.device) # rating>3 like=1, <=3 dislike=0
        target = torch.from_numpy(test['rating'].values).to(self.device) # rating>3 like=1, <=3 dislike=0
        itemage = torch.from_numpy(np.array(test['ItemAge'].values, dtype=int)).to(self.device)
        timestamp = torch.from_numpy(np.array(test['timestamp'].values, dtype=int)).to(self.device)

        interaction = {}
        interaction['user'] = uid_list
        interaction['item'] = iid_list
        interaction['target'] = target
        interaction['itemage'] = itemage
        interaction['timestamp'] = timestamp
        interaction['num'] = uid_list.size()[0]
        return interaction
    
    @torch.no_grad()
    def _eval_epoch(self, interaction, batch_size=512):
        users = interaction['user']
        items = interaction['item']
        itemages = interaction['itemage']
        num = interaction['num']
        scores = []
        start_idx, end_idx = 0, batch_size
        while start_idx < num:
            score = self.model.predict({'user':users[start_idx:end_idx], \
                'item':items[start_idx:end_idx], 'itemage':itemages[start_idx:end_idx]})
            # print(score)
            # exit(0)
            scores.append(score)
            start_idx = end_idx
            end_idx += batch_size
        return torch.cat(scores, 0)

    @torch.no_grad() 
    def evaluate(self, ub='false', threshold=1e-3, baselines = None, subset = None):
        interaction = self._data_pre_next_month()
        scores = self._eval_epoch(interaction)
        targets = interaction['target']
        results = cal_ratpred_metrics(scores.cpu().numpy(), targets.cpu().numpy())
        print('\t'.join(results.keys()), '\n', '\t'.join([str(x) for x in results.values()]))

        self._save_something(preds=scores.cpu().numpy())
        # # evaluate it as a ranking task
        # results = cal_ob_pred2ranking_metrics(interaction, scores)
        # print('\t'.join(results.keys()), '\n', '\t'.join([str(x) for x in results.values()]))
        # # print("results on testset: %s" % (str(results)))
        return results

    def _save_something(self, preds=None, target=None):
        # # only for tmf_v w/ global_offset_T
        # global_offset_T = self.model.global_T.weight.data.cpu().numpy().squeeze()
        # print(global_offset_T)
        # # look at the distributions on the preds per T
        print(len(preds), len(self.test))
        age = np.arange(self.data.n_periods)
        avg_T = []
        for T in age:
            # print(self.test['ItemAge'] == T)
            avg_T.append((preds[(self.test['ItemAge'] == T).values]).mean())
        print(avg_T)
        # preds = preds.clip(min(targets), max(targets))
        # preds = np.round(preds * 2) / 2.0
        # self.test['pred'] = preds
