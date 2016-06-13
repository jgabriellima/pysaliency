from __future__ import absolute_import, division, print_function, unicode_literals

from abc import abstractmethod

import numpy as np

from .generics import progressinfo
from .saliency_map_models import GeneralSaliencyMapModel, SaliencyMapModel, handle_stimulus
from .datasets import FixationTrains
from .utils import Cache
#from .saliency_map_conversion import JointSaliencyConvertor


def sample_from_image(densities, count=None):
    height, width = densities.shape
    sorted_densities = densities.flatten(order='C')
    cumsums = np.cumsum(sorted_densities)
    if count is None:
        real_count = 1
    else:
        real_count = count
    sample_xs = []
    sample_ys = []
    tmps = np.random.rand(real_count)
    js = np.searchsorted(cumsums, tmps)
    for j in js:
        sample_xs.append(j % width)
        sample_ys.append(j // width)
    sample_xs = np.asarray(sample_xs)
    sample_ys = np.asarray(sample_ys)
    if count is None:
        return sample_xs[0], sample_ys[0]
    else:
        return np.asarray(sample_xs), np.asarray(sample_ys)


class GeneralModel(GeneralSaliencyMapModel):
    """
    General probabilistic saliency model.

    Inheriting classes have to implement `conditional_log_density`
    """

    @abstractmethod
    def conditional_log_density(self, stimulus, x_hist, y_hist, t_hist, out=None):
        raise NotImplementedError()

    def conditional_saliency_map(self, stimulus, x_hist, y_hist, t_hist, out=None):
        return self.conditional_log_density(stimulus, x_hist, y_hist, t_hist, out=out)

    def log_likelihoods(self, stimuli, fixations, verbose=False):
        log_likelihoods = np.empty(len(fixations.x))
        for i in progressinfo(range(len(fixations.x)), verbose=verbose):
            conditional_log_density = self.conditional_log_density(stimuli.stimulus_objects[fixations.n[i]],
                                                                   fixations.x_hist[i],
                                                                   fixations.y_hist[i],
                                                                   fixations.t_hist[i])
            log_likelihoods[i] = conditional_log_density[fixations.y_int[i], fixations.x_int[i]]

        return log_likelihoods

    def log_likelihood(self, stimuli, fixations, verbose=False):
        return np.mean(self.log_likelihoods(stimuli, fixations, verbose=verbose))

    def _expand_sample_arguments(self, stimuli, train_counts, lengths=None, stimulus_indices=None):
        if isinstance(train_counts, int):
            train_counts = [train_counts]*len(stimuli)

        if stimulus_indices is None:
            if len(train_counts) > len(stimuli):
                raise ValueError('Number of train counts higher than count of stimuli!')
            stimulus_indices = range(len(train_counts))

        if isinstance(stimulus_indices, int):
            stimulus_indices = [stimulus_indices]

        if len(stimulus_indices) != len(train_counts):
            raise ValueError('Number of train counts must match number of stimulus_indices!')

        if isinstance(lengths, int):
            lengths = [lengths for i in range(len(stimuli))]

        if len(train_counts) != len(lengths):
            raise ValueError('Number of train counts and number of lengths do not match!')

        new_lengths = []
        for k, l in enumerate(lengths):
            if isinstance(l, int):
                ll = [l for i in range(train_counts[k])]
            else:
                ll = l
            new_lengths.append(ll)
        lengths = new_lengths

        for k, (c, ls) in enumerate(zip(train_counts, lengths)):
            if c != len(ls):
                raise ValueError('{}th train count ({}) does not match given number of lengths ({})!'.format(k, c, len(ls)))

        return stimuli, train_counts, lengths, stimulus_indices

    def sample(self, stimuli, train_counts, lengths=1, stimulus_indices=None):
        """
        Sample fixations for given stimuli


        Examples
        --------

        >>> model.sample(stimuli, 10)  # sample 10 fixations per image
        >>> # sample 5 fixations from the first image, 10 from the second and
        >>> # 30 fixations from the third image
        >>> model.sample(stimuli, [5, 10, 30])
        >>> # Sample 10 fixation trains per image, each consiting of 5 fixations
        >>> model.sample(stimuli, 10, lengths=5)
        >>> # Sample 10 fixation trains per image, consisting of 2 fixations
        >>> # for the first image, 4 fixations for the second, ...
        >>> model.sample(stimuli, 10, lengths=[2, 4, 3])
        >>> # Sample
        >>> model.sample(stimuli, 3, lengths=[[1,2,3], [1,2,3]])

        >>> # Sample 3 fixations from the 10th stimulus
        >>> model.sample(stimuli, 3, stimulus_indices = 10)

        >>> # Sample 3 fixations from the 20th and the 42th stimuli each
        >>> model.sample(stimuli, 3, stimulus_indices = [20, 42])
        """

        stimuli, train_counts, lengths, stimulus_indices = self._expand_sample_arguments(stimuli,
                                                                                         train_counts,
                                                                                         lengths,
                                                                                         stimulus_indices)

        xs = []
        ys = []
        ts = []
        ns = []
        subjects = []
        for stimulus_index, ls in progressinfo(zip(stimulus_indices, lengths)):
            stimulus = stimuli[stimulus_index]
            for l in ls:
                this_xs, this_ys, this_ts = self._sample_fixation_train(stimulus, l)
                xs.append(this_xs)
                ys.append(this_ys)
                ts.append(this_ts)
                ns.append(stimulus_index)
                subjects.append(0)
        return FixationTrains.from_fixation_trains(xs, ys, ts, ns, subjects)

    def _sample_fixation_train(self, stimulus, length):
        """Sample one fixation train of given length from stimulus"""
        xs = []
        ys = []
        ts = []
        for i in range(length):
            log_densities = self.conditional_log_density(stimulus, xs, ys, ts)
            x, y = sample_from_image(np.exp(log_densities))
            xs.append(x)
            ys.append(y)
            ts.append(len(ts))

        return xs, ys, ts


class Model(GeneralModel, SaliencyMapModel):
    """
    Time independend probabilistic saliency model.

    Inheriting classes have to implement `_log_density`.
    """
    def __init__(self, cache_location=None):
        super(Model, self).__init__(cache_location=cache_location)
        #self._log_density_cache = Cache(cache_location)
        # This make the property `cache_location` work.
        #self._saliency_map_cache = self._log_density_cache

    def conditional_log_density(self, stimulus, x_hist, y_hist, t_hist, out=None):
        return self.log_density(stimulus)

    def log_density(self, stimulus):
        """
        Get log_density for given stimulus.

        To overwrite this function, overwrite `_log_density` as otherwise
        the caching mechanism is disabled.
        """
        stimulus = handle_stimulus(stimulus)
        stimulus_id = stimulus.stimulus_id
        if not stimulus_id in self._cache:
            self._cache[stimulus_id] = self._log_density(stimulus.stimulus_data)
        return self._cache[stimulus_id]

    @abstractmethod
    def _log_density(self, stimulus):
        """
        Overwrite this to implement you own SaliencyMapModel.

        Parameters
        ----------

        @type  stimulus: ndarray
        @param stimulus: stimulus for which the saliency map should be computed.
        """
        raise NotImplementedError()

    def saliency_map(self, stimulus):
        return self.log_density(stimulus)

    def _saliency_map(self, stimulus):
        # We have to implement this abstract method
        pass

    def _sample_fixation_train(self, stimulus, length):
        """Sample one fixation train of given length from stimulus"""
        # We could reuse the implementation from `GeneralModel`
        # but this implementation is much faster for long trains.
        log_densities = self.log_density(stimulus)
        xs, ys = sample_from_image(np.exp(log_densities), count=length)
        ts = np.arange(len(xs))
        return xs, ys, ts

    def pixel_space_information_gain(self, baseline, gold_standard, stimulus, eps=1e-20):
        log_p_gold = gold_standard.log_density(stimulus)
        log_p_baseline = baseline.log_density(stimulus)
        log_p_model = self.log_density(stimulus)
        p_gold = np.exp(log_p_gold)
        p_gold[p_gold == 0] = p_gold[p_gold > 0].min()
        ig = (p_gold)*(np.logaddexp(log_p_model, np.log(eps))-np.logaddexp(log_p_baseline, np.log(eps)))
        return ig

    def kl_divergences(self, stimuli, gold_standard):
        """Calculate KL Divergence between model and gold standard for each stimulus.

        This metric works only for probabilistic models.
        For the existing saliency metrics known as KL Divergence, see
        `image_based_kl_divergence` and `fixation_based_kl_divergence`.
        """
        assert isinstance(self, Model)
        assert isinstance(gold_standard, Model)
        kl_divs = []
        for s in stimuli:
            logp_model = self.log_density(s)
            logp_gold = gold_standard.log_density(s)
            kl_divs.append((np.exp(logp_gold)*(logp_gold - logp_model)).sum())
        return kl_divs


class CachedModel(Model):
    """Density model which uses only precached densities
    """
    def __init__(self, cache_location, **kwargs):
        if cache_location is None:
            raise ValueError("CachedModel needs a cache location!")
        super(CachedModel, self).__init__(cache_location=cache_location, **kwargs)

    def _log_density(self, stimulus):
        raise NotImplementedError()


class UniformModel(Model):
    """Saliency model assuming uniform fixation distribution over space
    """
    def _log_density(self, stimulus):
        return np.zeros((stimulus.shape[0], stimulus.shape[1])) - np.log(stimulus.shape[0]) - np.log(stimulus.shape[1])

    def log_likelihoods(self, stimuli, fixations, verbose=False):
        lls = []
        for n in fixations.n:
            lls.append(-np.log(stimuli.shapes[n][0]) - np.log(stimuli.shapes[n][1]))
        return np.array(lls)

    #TODO: AUC, etc.

#class JointGoldStandard(JointSaliencyConvertor):
#    """
#    A gold standard model including blur, centerbias and nonlinearity, jointly optimized in cross validation
#    """
#    def __init__(self, *args, **kwargs):
#        super(self, JointSaliencyConvertor).__init__([], *args, **kwargs)
#
#    def fit(self, stimuli, fixations, crossvalidation_fold = 10):
#        pass

