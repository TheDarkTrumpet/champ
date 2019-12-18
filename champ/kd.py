import sys
import os
from scipy.optimize import minimize, curve_fit
import numpy as np
import matplotlib.pyplot as plt
from champ import misc
import itertools
from champ import seqtools


class KdFitIA(object):
    """
    A class to fit Kd Values.

    Input:
        Intensity Array:    Data structure with all intensity values
        [max_clust=2000]:   max clusters to use per fit
    """

    def __init__(self, IA, max_clust=2000):
        self.IA = IA
        assert self.IA.course_trait_name == 'concentration_pM', self.IA.course_trait_name
        self.concentrations = self.IA.course_trait_list
        self.nM_concentrations = self.concentrations
        self.nM_concentrations = [conc / 1000.0 for conc in self.concentrations]
        self.target = self.IA.target
        self.neg_control_target = self.IA.neg_control_target
        self.max_clust = max_clust
        self.Imin_names = ['Imin_const']
        self.Imax_names = ['Imax_adjusted']

    def add_Imin_type(self, Imin_name):
        """
        Optionally add more Imin-determining strategies. Default: Imin_const.
        """
        assert Imin_name in ['Imin_const', 'Imin_neg_cont'], Imin_name
        if Imin_name not in self.Imin_names:
            self.Imin_names.append(Imin_name)

    def add_Imax_type(self, Imax_name):
        """
        Optionally add more Imax-determining strategies. Default: Imax_adjusted.
        """
        assert Imax_name in ['Imax_const', 'Imax_adjusted', 'Imax_ML'], Imax_name
        if Imax_name not in self.Imax_names:
            self.Imax_names.append(Imax_name)

    def find_Imin_and_background_noise(self):
        """
        Find Imin and Imin_stdev, the mode and stdev of the negative control intensities at each
        concentration.
        """
        self.Imin_neg_cont = self.IA.modes_given_seq(self.neg_control_target)
        self.Imin_const = self.Imin_neg_cont[0]
        self.Imin_given_conc = {
            conc: Imin for conc, Imin in zip(self.concentrations, self.Imin_neg_cont)
            }
        self.Imin_stdev = self.IA.stdevs_given_seq(self.neg_control_target)
        self.Imin_stdev_given_conc = {
            conc: Imin_stdev for conc, Imin_stdev in zip(self.concentrations, self.Imin_stdev)
            }

    def find_Imax(self, ML_seqs=None):
        """
        Find Imax values according to selected strategies. If ML_seqs are included, finds Imax_ML.
        """

        def Iobs(x, Kd, Imax):
            return (Imax - self.Imin_const) / (1.0 + (float(Kd) / x)) + self.Imin_const

        all_concentrations, all_intensities = self.IA.all_trait_and_inten_vals_given_seq(
            self.target,
            max_clust=self.max_clust
        )

        popt, pcov = curve_fit(Iobs, all_concentrations, all_intensities)
        perfect_Kd, self.Imax_const = popt

        perfect_medians = self.IA.medians_given_seq(self.target)
        self.Imax_adjusted = []
        for conc, Imin, med in zip(self.concentrations, self.Imin_neg_cont, perfect_medians):
            fit_Iobs = Iobs(conc, perfect_Kd, self.Imax_const)
            if fit_Iobs < 0.90 * self.Imax_const:
                self.Imax_adjusted.append(self.Imax_const)
            else:
                # Using the median value as real Iobs, solve for Imax_adjusted
                self.Imax_adjusted.append(Imin + (1 + (perfect_Kd / conc)) * (med - Imin))
        self.Imax_given_conc = {
            conc: Imax for conc, Imax in zip(self.concentrations, self.Imax_adjusted)
            }

        if ML_seqs:
            self.fit_Imax_ML(ML_seqs)

    def model_logL(self,
                   seqs,
                   Kds,
                   Imin_list,
                   Imax_list,
                   sigma_consts,
                   sigI,
                   max_clust=None,
                   bootstrap_idxs=None):
        """
        Returns model logL probability. See documentation.
        """
        if bootstrap_idxs is not None:
            assert len(seqs) == 1, 'Bootstrapping can only be performed on one seq at a time.'
        if max_clust is None:
            max_clust = self.max_clust
        assert len(Imax_list) == len(self.concentrations), Imax_list
        Imax_arr = np.array(Imax_list)

        def theta(x, Kd):
            return 1.0 / (1.0 + (float(Kd) / x))

        thetas = np.empty((len(Kds), len(Imax_arr)))
        for i, Kd in enumerate(Kds):
            thetas[i] = [theta(conc, Kd) for conc in self.concentrations]
        sigma_clusts = (
            np.tile(self.Imin_stdev, (len(Kds), 1))
            + np.multiply(thetas, np.tile(sigma_consts, (len(Kds), 1)))
        )
        yhat = (
            np.tile(Imin_list, (len(Kds), 1))
            + np.multiply(thetas, np.tile(Imax_arr, (len(Kds), 1)))
        )
        logL = 0
        for sidx, seq in enumerate(seqs):
            loarr = self.IA.intensity_loarr_given_seq[seq]
            lol = self.IA.intensity_lol_given_seq[seq]
            for cidx, (inten_arr, inten_list) in enumerate(zip(loarr, lol)):
                if bootstrap_idxs is not None:
                    inten_arr = np.array([inten_list[idx] for idx in bootstrap_idxs
                                          if inten_list[idx] is not None])
                else:
                    inten_arr = inten_arr[:max_clust]
                logL += (
                    -len(inten_arr) * np.log(sigma_clusts[sidx, cidx])
                    - 1.0 / (2.0 * sigma_clusts[sidx, cidx] ** 2)
                    * np.square(inten_arr - yhat[sidx, cidx]).sum()
                )
        logL += (
            - len(Imax_arr) * np.log(sigI)
            - 1.0 / (2.0 * sigI ** 2) * np.square(Imax_arr - self.Imax_const).sum()
        )
        return logL

    def fit_Imax_ML_given_Imin(self, ML_seqs, Imin):
        idx1 = len(ML_seqs)
        idx2 = idx1 + self.IA.course_len
        idx3 = idx2 + self.IA.course_len
        Imin_list = misc.list_if_scalar(Imin, len(self.concentrations))

        def neg_log_L(params):
            params = map(abs, params)
            Kds = params[:idx1]
            Imax_list = params[idx1:idx2]
            sig_cs = params[idx2:idx3]
            sigI = params[idx3]
            return -self.model_logL(ML_seqs, Kds, Imin_list, Imax_list, sig_cs, sigI)

        x0 = list(
            [self.curve_fit_Kd(seq, self.Imin_const, self.Imax_const) for seq in ML_seqs]
            + self.Imax_adjusted
            + [1] * self.IA.course_len
            + [(self.Imax_const - self.Imin_const) / 10]
        )

        assert len(x0) == idx3 + 1
        res = minimize(neg_log_L, x0=x0, method='powell', options=dict(maxiter=1000000,
                                                                       maxfev=1000000,
                                                                       disp=True))
        # TODO - Verify the below isn't needed
        """ 
        if not res.success:
            print("\nWarning: Failure on {} ({})").format(seq, seqtools.mm_names(self.target, seq)
        """
        params = map(abs, res.x)
        Kds = params[:idx1]
        Imax_list = params[idx1:idx2]
        sig_cs = params[idx2:idx3]
        sigI = params[idx3]
        return Kds, Imax_list, sig_cs, sigI

    def fit_Imax_ML(self, ML_seqs):
        self.Imax_ML, self.sigma_consts, self.sigma_I = {}, {}, {}
        for Imin_name in self.Imin_names:
            Imin = getattr(self, Imin_name)
            Kds, Imax_list, sig_cs, sigI = self.fit_Imax_ML_given_Imin(ML_seqs, Imin)
            self.Imax_ML[Imin_name] = Imax_list
            self.sigma_consts[Imin_name] = sig_cs
            self.sigma_I[Imin_name] = sigI

    def ML_fit_Kd(self, seq, Imin_name, max_clust=None, bootstrap=False, *args, **kw_args):
        if max_clust is None:
            max_clust = self.max_clust
        if bootstrap:
            len_inten_list = len(self.IA.intensity_lol_given_seq[seq][0])
            bootstrap_idxs = np.random.choice(
                np.arange(len_inten_list),
                size=min(len_inten_list, max_clust),
                replace=True
            )
        else:
            bootstrap_idxs = None

        Imin = getattr(self, Imin_name)
        Imin_list = misc.list_if_scalar(Imin, len(self.concentrations))

        def neg_log_L(Kd):
            return -self.model_logL([seq],
                                    [Kd],
                                    Imin_list,
                                    self.Imax_ML[Imin_name],
                                    self.sigma_consts[Imin_name],
                                    self.sigma_I[Imin_name],
                                    bootstrap_idxs=bootstrap_idxs)

        res = minimize(neg_log_L, x0=20, method='powell', options=dict(maxiter=1000000,
                                                                       maxfev=1000000))
        if not res.success:
            print("\nWarning: Failure on {} ({})").format(seq, seqtools.mm_names(self.target, seq))
        return float(res.x)

    def curve_fit_Kd(self, seq, Imin, Imax, max_clust=None, bootstrap=False, *args, **kw_args):
        """
        Least square curve fit to values normalized by Imin/Imax.
        """
        if max_clust is None:
            max_clust = self.max_clust

        def Iobs(x, Kd):
            return 1.0 / (1 + (float(Kd) / x))

        all_concentrations, all_intensities = self.IA.all_normalized_trait_and_inten_vals_given_seq(
            seq,
            Imin,
            Imax,
            max_clust=max_clust,
            bootstrap=bootstrap
        )
        popt, pcov = curve_fit(Iobs, all_concentrations, all_intensities, maxfev=100000)
        return popt[0]

    def setup_for_fit(self, force=False):
        if hasattr(self, 'fit_func_given_Imin_max_names') and not force:
            return
        self.fit_func_given_Imin_max_names = {}
        self.Imin_max_pairs_given_names = {}
        for Imin_name, Imax_name in itertools.product(self.Imin_names, self.Imax_names):
            Imin = getattr(self, Imin_name)
            Imax = getattr(self, Imax_name)
            self.fit_func_given_Imin_max_names[(Imin_name, Imax_name)] = self.curve_fit_Kd
            self.Imin_max_pairs_given_names[(Imin_name, Imax_name)] = (Imin, Imax)

        if 'Imax_ML' in self.Imax_names:
            for Imin_name in self.Imin_names:
                Imin = getattr(self, Imin_name)
                Imax = self.Imax_ML[Imin_name]
                Imax_name = 'Imax_ML'
                self.fit_func_given_Imin_max_names[(Imin_name, Imax_name)] = self.ML_fit_Kd
                self.Imin_max_pairs_given_names[(Imin_name, Imax_name)] = (Imin, Imax)

    def fit_all_Kds(self, num_bootstraps=20):
        self.setup_for_fit()

        def ABA(Kd, neg_cont_Kd):
            return np.log(neg_cont_Kd) - np.log(Kd)

        self.Kds = {name_tup: [] for name_tup in self.fit_func_given_Imin_max_names.keys()}
        self.Kd_errors = {name_tup: [] for name_tup in self.fit_func_given_Imin_max_names.keys()}
        self.ABAs = {name_tup: [] for name_tup in self.fit_func_given_Imin_max_names.keys()}
        self.ABA_errors = {name_tup: [] for name_tup in self.fit_func_given_Imin_max_names.keys()}
        dot_val = 100
        print("{} Seqs, '.'={}\n").format(self.IA.nseqs, dot_val)
        for names_tup, (Imin, Imax) in sorted(self.Imin_max_pairs_given_names.items()):
            print("\n {names_tup}")
            Imin_name = names_tup[0]
            fit_func = self.fit_func_given_Imin_max_names[names_tup]
            neg_control_Kd = fit_func(self.neg_control_target,
                                      Imin=Imin,
                                      Imax=Imax,
                                      Imin_name=Imin_name)
            for i, seq in enumerate(self.IA.seqs):
                if i % dot_val == 0:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                Kd = fit_func(seq, Imin=Imin, Imax=Imax, Imin_name=Imin_name)
                bs_Kds = [fit_func(seq, Imin=Imin, Imax=Imax, Imin_name=Imin_name, bootstrap=True)
                          for _ in range(num_bootstraps)]
                self.Kds[names_tup].append(Kd)
                self.Kd_errors[names_tup].append(np.std(bs_Kds))
                self.ABAs[names_tup].append(ABA(Kd, neg_control_Kd))
                bs_ABAs = [ABA(kkd, neg_control_Kd) for kkd in bs_Kds]
                self.ABA_errors[names_tup].append(np.std(bs_ABAs))

    def write_results(self, out_dir, bname):
        for names_tup, (Imin, Imax) in sorted(self.Imin_max_pairs_given_names.items()):
            Imin_name, Imax_name = names_tup
            Imin = misc.list_if_scalar(Imin, len(self.concentrations))
            Imax = misc.list_if_scalar(Imax, len(self.concentrations))

            out_fname = '{}_{}_{}_Kds_and_ABAs.txt'.format(bname,
                                                           Imin_name.replace(' ', '_'),
                                                           Imax_name.replace(' ', '_'))
            out_fpath = os.path.join(out_dir, out_fname)
            with open(out_fpath, 'w') as out:
                out.write('# Target: {}\n'.format(self.target))
                out.write('# Neg Control: {}\n'.format(self.neg_control_target))
                out.write('# Concentration\tImin\tImax\n')
                for conc, imin, imax in zip(self.concentrations, Imin, Imax):
                    out.write('\t'.join(map(str, map(float, (conc, imin, imax)))) + '\n')
                out.write('\t'.join(['# Seq', 'Kd (pM)', 'Kd error', 'ABA (kB T)', 'ABA error']) + '\n')
                out_zipper = zip(
                    self.IA.seqs,
                    self.Kds[names_tup],
                    self.Kd_errors[names_tup],
                    self.ABAs[names_tup],
                    self.ABA_errors[names_tup]
                )
                out.write(
                    '\n'.join(
                        '\t'.join(map(str, [seq, Kd, Kd_err, ABA, ABA_err]))
                        for seq, Kd, Kd_err, ABA, ABA_err in out_zipper
                    )
                )

    def plot_raw_fit(self, ax, seq, Kd, Imin, Imax):
        self.IA.plot_raw_intensities(ax, seq, xvals=self.nM_concentrations)
        Imin = misc.list_if_scalar(Imin, self.IA.course_len)
        Imax = misc.list_if_scalar(Imax, self.IA.course_len)
        nM_Kd = Kd / 1000.0

        ax.plot(self.nM_concentrations, Imin, 'ko', alpha=0.8, label='$I_{min}$')
        ax.plot(self.nM_concentrations, Imax, 's', color='darkgoldenrod', alpha=0.8, label='$I_{max}$')

        def Iobs(x, Kd, Imin, Imax):
            return (Imax - Imin) / (1.0 + (float(Kd) / x)) + Imin

        fit_path = [Iobs(conc, nM_Kd, imn, imx)
                    for conc, imn, imx in zip(self.nM_concentrations, Imin, Imax)]
        ax.plot(self.nM_concentrations, fit_path, 'k--')

        x = np.logspace(np.log10(self.nM_concentrations[0]),
                        np.log10(self.nM_concentrations[-1]),
                        200)
        y = [Iobs(xx, nM_Kd, self.Imin_const, self.Imax_const) for xx in x]
        ax.plot(x, y, 'r')
        ax.set_xscale('log')

        ax.set_xlabel('Concentration (nM)', fontsize=18)
        ax.set_axis_bgcolor('white')
        ax.grid(False)
        ax.set_ylabel('Intensity', fontsize=18)

    def plot_normalized_fit(self, ax, seq, Kd, Imin, Imax):
        self.IA.plot_normalized_intensities(ax, seq, Imin, Imax, xvals=self.nM_concentrations)
        nM_Kd = Kd / 1000.0

        def Iobs(x, Kd):
            return 1.0 / (1.0 + (float(Kd) / x))

        x = np.logspace(np.log10(self.nM_concentrations[0]),
                        np.log10(self.nM_concentrations[-1]),
                        200)
        y = [Iobs(xx, nM_Kd) for xx in x]
        ax.plot(x, y, 'r')
        ax.set_xscale('log')

        ax.set_xlabel('Concentration (nM)', fontsize=18)
        ax.set_axis_bgcolor('white')
        ax.grid(False)
        ax.set_ylabel('Intensity', fontsize=18)

    def example_plots(self, seqs, labels=None):
        self.setup_for_fit()
        if labels is None:
            labels = [None] * len(seqs)
        for seq, label in zip(seqs, labels):
            fig, ax = plt.subplots(figsize=(12, 0.8))
            ax.text(0, 0, label, fontsize=20, ha='center', va='center')
            ax.set_xlim((-1, 1))
            ax.set_ylim((-1, 1))
            ax.set_xticks([])
            ax.set_yticks([])
            ax.grid(False)

            for names_tup, fit_func in self.fit_func_given_Imin_max_names.items():
                Imin_name = names_tup[0]
                fig, axes = plt.subplots(1, 2, figsize=(12, 5))
                Imin, Imax = self.Imin_max_pairs_given_names[names_tup]
                Kd = fit_func(seq, Imin=Imin, Imax=Imax, Imin_name=Imin_name)
                self.plot_raw_fit(axes[0], seq, Kd, Imin, Imax)
                self.plot_normalized_fit(axes[1], seq, Kd, Imin, Imax)
                axes[0].set_title('%s, Kd = %.2f' % (names_tup, Kd / 1000.0))
                axes[1].set_title(label)

    def all_error_analysis_and_figs(self, *args, **kw_args):
        self.setup_for_fit()
        for names_tup in self.Imin_max_pairs_given_names.keys():
            print(F"names_tup: {names_tup}")
            sys.stdout.flush()
            self.error_analysis_and_figs(names_tup, *args, **kw_args)

    def error_analysis_and_figs(self,
                                Imin_max_names_tup,
                                seq=None,
                                num_bootstraps=100,
                                conf_pct=90,
                                min_reads=5,
                                out_dir=None,
                                out_bname=None):
        """
        For the given sequence, performs bootstrap analysis of errors for all numbers of clusters
        from 3 to 100.
        """
        if seq is None:
            seq = self.target

        fit_func = self.fit_func_given_Imin_max_names[Imin_max_names_tup]
        Imin, Imax = self.Imin_max_pairs_given_names[Imin_max_names_tup]
        Imin_name, Imax_name = Imin_max_names_tup

        ref_Kd = fit_func(seq, Imin=Imin, Imax=Imax, Imin_name=Imin_name)
        ref_dG = np.log(ref_Kd)

        read_names = self.IA.read_names_given_seq[seq]
        nclusters = range(3, min(100, len(read_names)))
        Kd_avg_errors, dG_avg_errors = [], []
        Kd_conf_errors, dG_conf_errors = [], []
        for n in nclusters:
            sys.stdout.write('.')
            sys.stdout.flush()
            bs_Kds = [fit_func(seq, Imin=Imin, Imax=Imax, Imin_name=Imin_name, max_clust=n, bootstrap=True)
                      for _ in range(num_bootstraps)]
            bs_Kd_errors = [abs(ref_Kd - Kd) / 1000.0 for Kd in bs_Kds]
            bs_dG_errors = [abs(ref_dG - np.log(Kd)) for Kd in bs_Kds]
            Kd_avg_errors.append(np.average(bs_Kd_errors))
            Kd_conf_errors.append(np.percentile(bs_Kd_errors, conf_pct))
            dG_avg_errors.append(np.average(bs_dG_errors))
            dG_conf_errors.append(np.percentile(bs_dG_errors, conf_pct))
        print

        def c_over_sqrt_n(n, c):
            return c / np.sqrt(n)

        def fit_c_over_sqrt_n(ns, data):
            new_ns = [n for n, dd in zip(ns, data) if np.isfinite(n) and np.isfinite(dd) and n > 10]
            new_data = [dd for n, dd in zip(ns, data) if np.isfinite(n) and np.isfinite(dd) and n > 10]
            popt, pcov = curve_fit(c_over_sqrt_n, new_ns, new_data, maxfev=10000)
            return popt[0]

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))
        for ax, label, units, avg_errors, conf_errors in zip(axes,
                                                             ('$K_d$', 'ABA'),
                                                             ('nM', '$k_B T$'),
                                                             (Kd_avg_errors, dG_avg_errors),
                                                             (Kd_conf_errors, dG_conf_errors)):
            fit_ns = np.linspace(1, max(nclusters), 300)
            c_avg = fit_c_over_sqrt_n(nclusters, avg_errors)
            c_conf = fit_c_over_sqrt_n(nclusters, conf_errors)
            avg_fit_vals = [c_over_sqrt_n(n, c_avg) for n in fit_ns]
            conf_fit_vals = [c_over_sqrt_n(n, c_conf) for n in fit_ns]

            min_reads_avg_fit = c_over_sqrt_n(min_reads, c_avg)

            ax.plot(nclusters, avg_errors, '.', label='Average Error')
            ax.plot(fit_ns, avg_fit_vals, label='Average Fit = $%.2f / \sqrt{n}$' % c_avg)
            ax.plot(nclusters, conf_errors, '.', label='90% Confidence Interval')
            ax.plot(fit_ns, conf_fit_vals, '--', label='90%% Conf Interval Fit = $%.2f / \sqrt{n}$' % c_conf)
            ax.plot([0, min_reads, min_reads], [min_reads_avg_fit, min_reads_avg_fit, 0], ':k')
            ax.set_xlim((0, 100))
            ax.set_xlabel('Number of clusters', fontsize=18)
            ax.set_ylabel('{} Error ({})'.format(label, units), fontsize=18)
            ax.legend(fontsize=14)
            ax.get_legend().get_frame().set_facecolor('white')
            ax.set_axis_bgcolor('white')
            ax.grid(False)

            for item in ax.get_xticklabels() + ax.get_yticklabels():
                item.set_fontsize(16)

        if out_dir:
            out_bname = '{}_{}_{}_error_analysis'.format(out_bname,
                                                         Imin_name.replace(' ', '_'),
                                                         Imax_name.replace(' ', '_'))
            fig.savefig(os.path.join(out_dir, out_bname + '.png'), dpi=300)
            fig.savefig(os.path.join(out_dir, out_bname + '.eps'))


class IAKdData(object):
    def __init__(self, Kd_fpath):
        self.concentrations, self.Imin, self.Imax, = [], [], []
        self.Kd, self.Kd_error, self.ABA, self.ABA_error = {}, {}, {}, {}
        with open(Kd_fpath) as f:
            line = next(f)
            assert line.startswith('# Target:')
            self.target = line.strip().split(': ')[1]
            line = next(f)
            assert line.startswith('# Neg Control')
            self.neg_control_target = line.strip().split(': ')[1]
            line = next(f)
            assert line.startswith('# Concentration\tImin\tImax')
            line = next(f)
            while not line.startswith('#'):
                conc, imn, imx = map(float, line.strip().split())
                self.concentrations.append(conc)
                self.Imin.append(imn)
                self.Imax.append(imx)
                line = next(f)
            assert line.startswith('# Seq')
            for line in f:
                if line.startswith('#'):
                    continue
                words = line.strip().split()
                seq = words[0]
                assert seq not in self.Kd, seq
                Kd, Kd_err, ABA, ABA_err = map(float, words[1:])
                self.Kd[seq] = Kd
                self.Kd_error[seq] = Kd_err
                self.ABA[seq] = ABA
                self.ABA_error[seq] = ABA_err
        self.neg_control_Kd = self.Kd[self.neg_control_target]
        self.log_neg_control_Kd = np.log(self.neg_control_Kd)
        self.target_ABA = self.ABA[self.target]

    def ABA_given_Kd(self, Kd):
        if Kd is None:
            return None
        return self.log_neg_control_Kd - np.log(Kd)
