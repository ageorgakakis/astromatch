
"""
Python implementation of the likelihood ratio method for
cross-matching two astronomical catalogues.

References: Sutherland & Saunders, 1992; Georgakakis & Nandra 2011

author: A.Ruiz & A.Georgakakis
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from io import open

import os
from copy import deepcopy

try:
    # python 3
    from contextlib import redirect_stdout
except:
    # python 2
    from contextlib2 import redirect_stdout

from astropy import log
from astropy import units as u
from astropy.table import Table, Column, join, vstack, unique
import numpy as np

from .priors import Prior #, BKGpdf
from .priorsND import PriorND, BKGpdf
from .match import BaseMatch

import sys


class LRMatch(BaseMatch):
    """
    This class defines a crossmatching of two catalogues. It is initialized
    by passing two ``Catalogue`` objects:
    one for the primary catalogue (e.g. a list of X-ray sources) and another
    one for the secondary catalogue (e.g. a list of optical sources). The
    secondary catalogue must contain auxiliary data (e.g. magnitudes).

    Parameters
    ----------
    pcat : ``Catalogue``
        Primary catalogue.
    scat : ``Catalogue``
        Secondary catalogue.
    """

    _lr_all = None
    _bkg = None
    _cutoff_column = 'LR_BEST'

    ### Class Properties
    @property
    def lr(self):
        if self._lr_all is None:
            raise AttributeError('Match has not been performed yet!')
        else:
            return self._lr_all

    @property
    def bkg(self):
        if self._bkg is None:
            raise AttributeError('Match has not been performed yet!')
        else:
            return self._bkg

    @property
    def scat(self):
        return self.scats[0]

    ### Public Methods
    def run(self,
        radius=6*u.arcsec,
        mags=None,
        magmin=10.0,
        magmax=30.0,
        magbinsize=0.5,
        priors=None,
        prior_method='random',
        random_numrepeat=200,
        poserr_dist="rayleigh",
        prob_ratio_secondary=0.5,
        seed=None            
    ):
        """
        Performs the actual LR crossmatch between the two catalogues. The
        method identifies all possible counterparts of the primary catalogue
        in the secondary catalogue within `radius` and estimates the
        corresponding LR for each counterpart in each available magnitude.

        Parameters
        ----------
        radius : Astropy ``Quantity``, optional
            Distance limit for searching counterparts in the secondary
            catalogue in angular units. Defaults to 6 arcsec.
        mag: 
        magmin : `float`, optional
            Lower magnitude limit to be considered in the LR calculation.
            Defaults to 10.0.
        magmax : `float`, optional
            Upper magnitude limit to be considered in the LR calculation.
            Defaults to 30.0.
        magbinsize : `float`, optional
            Magnitude bin width when estimating magnitude distributions.
            Defaults to 0.5.
        prob_ratio_secondary : `float`, optional
            Minimum probability ratio between best and other matchs for a
            primary source to be considered as a secondary match.
            Defaults to 0.5.
        priors : ``PriorND`` object or `None`, optional
            Predefined Prior object to be used in the cross-match. It has to
            be defined consistently with the magnitudes of the secondary catalogue.
            If `None`, the method set in `prior_method` is used to build the priors.
            Defaults to `None`.
        prior_method : 'random' or 'mask', optional
            Method to be used in the prior estimation.
            The a priori probability is determined as follows. First, we estimate
            the magnitude distribution of the spurious matches and it is scaled
            to the area within which we search for counterparts. This is then
            subtracted from the magnitude distribution of all counterparts in the
            secondary catalogue to determine the magnitude distribution of the
            true associations.
            The 'mask' method removes all sources in the
            secondary catalogue within one arcmin of the positions of the
            primary sources. The magnitude distribution of the remaining sources,
            divided by the remaining catalogue area, corresponds to the
            probability distribution of a spurious match per magnitude and per
            square degree.
            The 'random' method generates a catalogue of random
            positions away from the primary sources and searchs for all available
            counterparts in the secondary catalogue. The magnitude distribution
            of these sources corresponds to the probability distribution of a
            spurious match.
            Defaults to 'random'.

        random_numrepeat : number of random realisations in the case of prior_method=random
            Defaults to 200.

        poserr_dist : "rayleigh" of "normal". Probability distribution that describes 
            the radial distance of two sources with positional errors. Defauls to "Rayleigh"

        """

        assert poserr_dist.lower() in ['normal', 'rayleigh'], "xposerr_dist  should be one of normal, rayleigh"
        assert prior_method.lower() in ['random', 'mask'], "prior_method  should be one of random, mask"
        
        

        self.poserr_dist= poserr_dist

        self.random_numrepeat = random_numrepeat

        
        if self.scat.mags is None:
            raise ValueError('Secondary catalogue must contain '
                             'auxiliary data (e.g. magnitudes).')
        self.radius = radius
        
        log.info('Searching for match candidates within {}...'.format(self.radius))
        mcat_pidx, mcat_sidx, mcat_d2d = self._candidates()

        log.info('Calculating priors...')        
        if not priors:
            self._priors = self._calc_priors(
                mcat_sidx, mags, magmin, magmax, magbinsize, prior_method, seed
            )
        else:            
            self._priors = priors

        self._bkg = BKGpdf(self.scat, mags, magmin, magmax, magbinsize)
        #sys.exit()
        log.info('Calculating likelihood ratios for match candidates...')
        lr, self._lr_all = self._likelihood_ratio(mcat_pidx, mcat_sidx, mcat_d2d)

        log.info('Sorting and flagging match results...')
        match = self._final_table(lr, prob_ratio_secondary)

        return match

    # Override the BaseMatch method
    def stats(
        self,
        match,
        ncutoff=101,
        mincutoff=0.0,
        maxcutoff=10.0,
        plot_to_file=None,
        only_primary=True
    ):
        """
        Calculates and store match statistics (completness and reliability)
        for a range of LR thresholds. This can be used later to select the
        optimal threshold.
        """
        # parametrise common statistics/definitions that
        # quantify the reliability and completeness of
        # the cross-match.
        # Produce table with summary of results
        # also include some reference ...?  (e.g. Luo et al 2010???)

        if only_primary:
            # We use only primary matches
            mask = match['match_flag'] == 1
        else:
            # All matches
            mask = match['ncat'] == 2

        lrdata = match[mask]

        stats = Table()
        stats['cutoff'] = np.linspace(mincutoff, maxcutoff, num=ncutoff)
        stats['completeness'] = np.nan
        stats['reliability'] = np.nan

        for i, lrlim in enumerate(stats['cutoff']):
            rel_good = lrdata['REL_BEST'][lrdata[self._cutoff_column] > lrlim]
            #CHILR[i] = float(rel_good.size)/len(self.pcat) # sample completeness
            #stats['CHILR'][i] = np.sum(rel_good)/len(self.pcat)  # completeness
            stats['completeness'][i] = float(rel_good.size)/len(lrdata) # completeness (AGE)
            stats['reliability'][i] = np.mean(rel_good)                # reliability

        stats['error_rate'] = 1 - stats['reliability']
        stats['CR'] = stats['completeness'] + stats['reliability']

        if plot_to_file is not None:
            self._plot_stats(stats, plot_to_file)

        return stats

    # Overrides the BaseMatch method
    def stats_rndmatch(
        self,
        match,
        match_rnd,
        ncutoff=101,
        mincutoff=0.0,
        maxcutoff=10.0,
        plot_to_file=None
    ):
        """
        Calculates match statistics (completness and reliability), using a
        random match, for a range of LR thresholds. This can be used later to
        select the optimal threshold.
        """
        # TODO: LR ouput should be improved to simplify these selection masks
        mask = np.logical_and(match['ncat'] == 2, match['match_flag'] == 1)
        p_any0 = match[self._cutoff_column][mask]

        # Add sources with no matches
        size = len(np.where(match['ncat'] == 1)[0]) - len(p_any0)
        p_any0 = np.concatenate((np.array(p_any0), np.zeros(size)))

        mask = np.logical_and(match_rnd['ncat'] == 2, match_rnd['match_flag'] == 1)
        p_any0_offset = match_rnd[self._cutoff_column][mask]

        # Add sources with no matches
        size = len(np.where(match_rnd['ncat'] == 1)[0]) - len(p_any0_offset)
        p_any0_offset = np.concatenate(
            (np.array(p_any0_offset), np.zeros(size))
        )

        cutoffs = np.linspace(mincutoff, maxcutoff, num=ncutoff)

        stats = Table()
        stats['cutoff'] = cutoffs
        stats['completeness'] = [(p_any0 > c).mean() for c in cutoffs]
        stats['error_rate'] = [(p_any0_offset > c).mean() for c in cutoffs]
        stats['reliability'] = 1 - stats['error_rate']
        stats['CR'] = stats['completeness'] + stats['reliability']

        return stats

    ### Internal Methods
    def _candidates(self):
        """
        Identify all possible counterparts for the primary sources in the
        secondary catalogue within `self.radius`.

        Returns
        -------
        pidx : numpy ``ndarray``
            Indexes of the primary sources with counterparts in the
            secondary catalogue.
        sidx : numpy ``ndarray``
            Indexes of the counterparts in the secondary catalogue.
        d2d : numpy ``ndarray``
            Distance between the primary source and the counterpart in the
            secondary catalogue.
        See the documentation of Astropy's ``search_around_sky`` tool for more
        details about the output.
        """
        pcoords = self.pcat.coords
        scoords = self.scat.coords
        pidx, sidx, d2d, _ = scoords.search_around_sky(pcoords, self.radius)
        #print(pidx, sidx, ded)
        return pidx, sidx, d2d

    def _calc_priors(self, sidx, mags, magmin, magmax, magbinsize, method, seed):
        """
        Estimates the prior probability distribution for a source in the
        primary catalogue having a counterpart in the secondary catalogue
        with magnitude m.

        The a priori probability is determined as follows. First, we estimate
        the magnitude distribution of the spurious matches and it is scaled
        to the area within which we search for counterparts. This is then
        subtracted from the magnitude distribution of all counterparts in the
        secondary catalogue to determine the magnitude distribution of the
        true associations.

        Parameters
        ----------
        sidx : numpy ``ndarray``
            Indexes of the counterparts in the secondary catalogue.
        mags:  'list' of catalogue column names or combination of them
            combinations are simply a 'list' of more than one
            names of catalogue columns
        magmin : `float`, optional
            Lower magnitude limit when estimating magnitude distributions.
        magmax : `float`, optional
            Upper magnitude limit when estimating magnitude distributions.
        magbinsize : `float`, optional
            Magnitude bin width when estimating magnitude distributions.
        method : 'random' or 'mask'
            Method for estimating the magnitude distribution of spurious
            matches. See the documentation of ``run`` method for details.

        Return
        ------
        priors : ``Prior``
            Object containing the magnitude probability distributions of "true"
            and "spurious" sources for each available magnitude in the
            secondary catalogue.
        """
        #print(magmin, magmax, magbinsize)
        
        if method == 'mask':
            rndcat = False
        elif method == 'random':
            rndcat = self.pcat.randomise(numrepeat=self.random_numrepeat, seed=seed)
        else:
            raise ValueError('Unknown method: {}'.format(method))

        priors = PriorND(self.pcat, self.scat, rndcat, self.radius, mags, 
                       magmin, magmax, magbinsize, self.scat.mags[sidx])

        #priors.plot("prior0")
        return priors

    def _likelihood_ratio(self, pidx, sidx, d2d):
        """
        Estimates the likelihood ratio for all counterparts and for each
        magnitude band.

        Parameters
        ----------
        pidx : numpy ``ndarray``
            Indexes of the primary sources with counterparts in the
            secondary catalogue.
        sidx : numpy ``ndarray``
            Indexes of the counterparts in the secondary catalogue.
        d2d : numpy ``ndarray``
            Distance between the primary source and the counterpart in the
            secondary catalogue.

        Return
        ------
        lr : Astropy ``Table``
            Table with the likelihood ratios for each counterpart and each
            available magnitude in the secondary catalogue.
        """
        pcat_idcol = 'SRCID_{}'.format(self.pcat.name)
        scat_idcol = 'SRCID_{}'.format(self.scat.name)
        drcol = 'Separation_{}_{}'.format(self.pcat.name, self.scat.name)

        lr = Table()
        lr[pcat_idcol] = self.pcat.ids[pidx]
        lr[scat_idcol] = self.scat.ids[sidx]
        lr[drcol] = d2d.to(u.arcsec)
        lr['ncat'] = 2
        lr['PEF'] = self._pos_err_function(d2d, pidx, sidx)
        #print(lr)
        #self._qnterms(lr, self.scat.mags[sidx])
        self._lrND(lr, self.scat.mags[sidx])

        # For estimating the reliability, the table has to be grouped.
        # Note that this changes the order of the rows!!!
        lr = lr.group_by(pcat_idcol)
        self._reliabilityND(lr)

        self._p_any_bestND(lr)
        lr_all = lr.copy()

        final_columns = [pcat_idcol, scat_idcol, drcol, 'ncat',
                         'LR_BEST', 'REL_BEST', 'LR_BEST_MAG',
                         'prob_has_match', 'prob_this_match',
                         ]
        lr.keep_columns(final_columns)

        return lr, lr_all

    ### methods for _likelihood_ratio
    def _pos_err_function(self, radius, pidx, sidx):

        if(self.poserr_dist.lower()=="normal"):
            return self._pos_err_function_normal(radius, pidx, sidx)
        elif(self.poserr_dist.lower()=="rayleigh"):
           return self._pos_err_function_rayleigh(radius, pidx, sidx)
        else:
            raise ValueError('Unknown method: {}'.format(self.poserr_dist))

        
    def _pos_err_function_rayleigh(self, radius, pidx, sidx):
        # radius is offset between opt/xray counter in arcsec
        # I assume that the pos is Gaussian.
        # NOTE: This returns prob per square *arcsec*  !!!!
        ppos_error = self.pcat.poserr[pidx].as_array()
        spos_error = self.scat.poserr[sidx].as_array()

        # Rayleigh
        sigma2 = ppos_error**2 + spos_error**2
        exponent = -radius**2 / sigma2        
        return np.exp(exponent) / sigma2 * 2 * radius

    
    def _pos_err_function_normal(self, radius, pidx, sidx):
        # radius is offset between opt/xray counter in arcsec
        # I assume that the pos is Gaussian.
        # NOTE: This returns prob per square *arcsec*  !!!!
        ppos_error = self.pcat.poserr[pidx].as_array()
        spos_error = self.scat.poserr[sidx].as_array()

        # Gaussian
        sigma2 = ppos_error**2 + spos_error**2
        exponent = -radius**2 / (2*sigma2)
        return np.exp(exponent) / (2*np.pi*sigma2)


    def _lrND(self, lr_table, mags):

        # here the loop is over .priors.dict_prior.keys
        # then for each [name] I define arrays
        # I define tuples and the get the results from
        # the prior. 
        
        for col in self._priors.prior_dict.keys():
            #print(col,  self._priors.prior_dict.keys()
            qterm = self._priors.interp(mags, col)
            nterm = self._bkg.interp(mags, col)

            m = nterm ==0            
            lr_table['LR_' + col] =  lr_table['PEF'] * qterm / nterm

            # if nterm == 0 then do not assign a lr
            lr_table['LR_' + col][m]=0.0
            

    def _reliabilityND(self, lr_table):
        group_size = np.diff(lr_table.groups.indices)

       

        QCAP = []
        for col in self._priors.prior_dict.keys():
            # Overall identification ratio
            q = self._priors.qcap(col)
            QCAP.append(q)

            # Add all values of LR for each group, i.e., all
            # matches for a source of the primary catalogue.
            sumlr = lr_table['LR_' + col].groups.aggregate(np.sum)
            #print(sumlr.shape)
            # The previous array has a length equal to the number of groups.
            # We need to rebuild the array having the same length as the
            # original table for using element-wise operations. We repeat
            # each element of sumlr as many times as the size of the
            # corresponding group
            sumlr = np.repeat(sumlr, group_size)
            #print(sumlr.shape)
            lr_table['REL_' + col] = lr_table['LR_' + col] / (sumlr + (1-q))

            # Probability that the primary source has a counterpart in magcol
            # i.e. the sum of reliabilities for all matches of a given source
            sumrel = lr_table['REL_' + col].groups.aggregate(np.sum)
            sumrel = np.repeat(sumrel, group_size)
            lr_table['p_any_' + col] = sumrel

            # Relative probability for a given counterpart
            lr_table['p_i_' + col] = (lr_table['REL_' + col] /
                                         lr_table['p_any_' + col])

        print(QCAP)
        #for g in lr_table.groups:
        #    print(lr_table['LR_' + col])
        
        lr_table.meta['QCAP'] = str(QCAP)
        #lr_table.write("e.fits", format='fits', overwrite=True)
        
    def _p_any_bestND(self, lr_table):
        pa_array = np.ndarray((len(lr_table), len(self._priors.prior_dict)))
        pi_array = np.ndarray((len(lr_table), len(self._priors.prior_dict)))
        lr_array = np.ndarray((len(lr_table), len(self._priors.prior_dict)))
        rel_array = np.ndarray((len(lr_table), len(self._priors.prior_dict)))

        names=[]
        for i, col in enumerate(self._priors.prior_dict.keys()):
            lr_array[:, i] = lr_table['LR_' + col]
            rel_array[:, i] = lr_table['REL_' + col]
            pa_array[:, i] = lr_table['p_any_' + col]
            pi_array[:, i] = lr_table['p_i_' + col]
            names.append(col)
        names=np.array(names)
        idx_pbest = np.argmax(pa_array, axis=1)
        #print(idx_pbest)
        #print(names[idx_pbest])
        uidx_pbest = (np.arange(len(lr_table)), idx_pbest)

        lr_table['LR_BEST'] = lr_array[uidx_pbest]
        lr_table['REL_BEST'] = rel_array[uidx_pbest]
        lr_table['LR_BEST_MAG'] = [names[i].encode('utf8')
                                   for i in idx_pbest]
        lr_table['prob_has_match'] = pa_array[uidx_pbest]
        lr_table['prob_this_match'] = pi_array[uidx_pbest]


    def plot_stats(self, stats, ext):
        
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.ticker import MultipleLocator, FormatStrFormatter

        plotfile = os.path.join(self.MHDR, self.dirs['FIELDS'], self.id, 'LR', 'stats-{}_{}.pdf'.format(self.id, ext))
          
        fontPanel = {'family': 'serif',
                     'color': 'black',
                     'weight': 'bold',
                     'size': 10,} 

        figall=[]
        pdf = PdfPages(plotfile)
        fig, ax = plt.subplots(1,1)
        y= stats['completeness']
        x= stats['cutoff']
        ax.plot(x,y, 'r-', label="completeness")            
        y= stats['reliability']
        ax.plot(x,y, 'b--', label="purity")            
        
        for axis in ['top','bottom','left','right']:
            for a in [ax]:
                a.spines[axis].set_linewidth(2)
                a.tick_params(labelsize=12)
                a.xaxis.set_tick_params(which='major', width=1.5, size=8)
                a.yaxis.set_tick_params(which='major', width=1.5, size=8)
                a.xaxis.set_tick_params(which='minor', width=1.5, size=5)
                a.yaxis.set_tick_params(which='minor', width=1.5, size=5)
         
        xmax=3
        m= stats['cutoff']<xmax
        min1 = numpy.min(stats['completeness'][m])
        min2 = numpy.min(stats['reliability'][m])
        min1 = min(min1,min2)
        max1 = numpy.max(stats['completeness'][m])
        max2 = numpy.max(stats['completeness'][m])
        max1 = max(max1,max2)

        ax.set_xlim(0,xmax)
        ax.set_ylim(min1*0.95, max1*1.05)
        ax.grid()
        ax.legend()
        #plt.show()
        pdf.savefig( fig )
        pdf.close()
 

    """
    # these are the old methods
    
    def _lr(self, lr_table, mags):

        for magcol in self.scat.mags.colnames:
            qterm = self._priors.interp(mags[magcol], magcol)
            nterm = self._bkg.interp(mags[magcol], magcol)

            lr_table['LR_' + magcol] =  lr_table['PEF'] * qterm / nterm

    def _reliability(self, lr_table):
        group_size = np.diff(lr_table.groups.indices)

        QCAP = []
        for magcol in self.scat.mags.colnames:
            # Overall identification ratio
            q = self._priors.qcap(magcol)
            QCAP.append(q)

            # Add all values of LR for each group, i.e., all
            # matches for a source of the primary catalogue.
            sumlr = lr_table['LR_' + magcol].groups.aggregate(np.sum)

            # The previous array has a length equal to the number of groups.
            # We need to rebuild the array having the same length as the
            # original table for using element-wise operations. We repeat
            # each element of sumlr as many times as the size of the
            # corresponding group
            sumlr = np.repeat(sumlr, group_size)

            lr_table['REL_' + magcol] = lr_table['LR_' + magcol] / (sumlr + (1-q))

            # Probability that the primary source has a counterpart in magcol
            # i.e. the sum of reliabilities for all matches of a given source
            sumrel = lr_table['REL_' + magcol].groups.aggregate(np.sum)
            sumrel = np.repeat(sumrel, group_size)
            lr_table['p_any_' + magcol] = sumrel

            # Relative probability for a given counterpart
            lr_table['p_i_' + magcol] = (lr_table['REL_' + magcol] /
                                         lr_table['p_any_' + magcol])

        lr_table.meta['QCAP'] = str(QCAP)

#    def _lr_best(self, lr_table):
#        lr_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))
#        rel_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))
#        for i, col in enumerate(self.scat.mags.colnames):
#            lr_array[:, i] = lr_table['LR_' + col]
#            rel_array[:, i] = lr_table['REL_' + col]
#
#        idx_lrbest = np.argmax(lr_array, axis=1)
#        uidx_lrbest = (np.arange(len(lr_table)), idx_lrbest)
#
#        lr_table['LR_BEST'] = lr_array[uidx_lrbest]
#        lr_table['REL_BEST'] = rel_array[uidx_lrbest]
#        lr_table['LR_BEST_MAG'] = [self.scat.mags.colnames[i].encode('utf8')
#                                   for i in idx_lrbest]
    
    def _p_any_best(self, lr_table):
        pa_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))
        pi_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))
        lr_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))
        rel_array = np.ndarray((len(lr_table), len(self.scat.mags.colnames)))

        for i, col in enumerate(self.scat.mags.colnames):
            lr_array[:, i] = lr_table['LR_' + col]
            rel_array[:, i] = lr_table['REL_' + col]
            pa_array[:, i] = lr_table['p_any_' + col]
            pi_array[:, i] = lr_table['p_i_' + col]

        idx_pbest = np.argmax(pa_array, axis=1)
        uidx_pbest = (np.arange(len(lr_table)), idx_pbest)

        lr_table['LR_BEST'] = lr_array[uidx_pbest]
        lr_table['REL_BEST'] = rel_array[uidx_pbest]
        lr_table['LR_BEST_MAG'] = [self.scat.mags.colnames[i].encode('utf8')
                                   for i in idx_pbest]
        lr_table['prob_has_match'] = pa_array[uidx_pbest]
        lr_table['prob_this_match'] = pi_array[uidx_pbest]

        
    ### ===
    """

    def _final_table(self, lr_table, prob_ratio_secondary):
        """
        Join LR results with previous data, add match flag and sort.
        """
        match = self._add_all_psources(lr_table)
        match = self._add_match_flags(match, prob_ratio_secondary)
        match = self._sort(match)

        return match

    ### methods for _final_table
    def _add_all_psources(self, lr_table):
        pcat_idcol = lr_table.colnames[0]

        matched_psrcs = unique(lr_table, keys=pcat_idcol)
        matched_psrcs.keep_columns([pcat_idcol, 'prob_has_match'])

        all_psrcs = Table()
        all_psrcs[pcat_idcol] = self.pcat.ids
        all_psrcs['ncat'] = 1
        all_psrcs['prob_this_match'] = 0.0

        all_psrcs = join(all_psrcs, matched_psrcs, keys=pcat_idcol, join_type='left')
        try:
            mask = all_psrcs['prob_has_match'].mask
            all_psrcs['prob_has_match'][mask] = 0.0
        except AttributeError:
            # This happens when all primary sources have counterparts
            pass

        return vstack([lr_table, all_psrcs])

    def _add_match_flags(self, match, prob_ratio_secondary):

        ## Add match_flag column, default value to zero
        idx_flag = match.colnames.index('prob_has_match')
        
        col_flag = Column(name='match_flag', data=[0]*len(match))
        match.add_column(col_flag, index=idx_flag)

        match = match.group_by(match.colnames[0])        
        group_size = np.diff(match.groups.indices)
        #print(match)

        
        ## For each primary source, find the match with maximum p_i
        pi_max = match['prob_this_match'].groups.aggregate(np.max)        
        
        # The previous array has a length equal to the number of groups.
        # We need to rebuild the array having the same length as the
        # original table for using element-wise operations. We repeat
        # each element of sumlr as many times as the size of the
        # corresponding group
        pi_max = np.repeat(pi_max, group_size)

        mask = match['prob_this_match'] == pi_max
        match['match_flag'][mask] = 1

        ## Find secondary matches
        #for i,j in zip(match['prob_this_match'],pi_max):
        #    print (i, j,  prob_ratio_secondary)
            
        mask_ratio = match['prob_this_match']/pi_max > prob_ratio_secondary
        mask = np.logical_and(~mask, mask_ratio)
        match['match_flag'][mask] = 2

        ## Flag as secondary matches with multiple counterparts
        ## with LR_BEST == 0 and pi_max == 0
        mask = np.logical_and(match['ncat'] > 1, match['LR_BEST'] == 0.0)
        mask = np.logical_and(mask, pi_max == 0)
        match['match_flag'][mask] = 2

        return match

    def _sort(self, match):
        # Sort final table as the primary catalogue.
        # We need to pass the column in pcat with the source ids.
        return self._sort_as_pcat(match, match.colnames[0])

    ### ===

    def _match_rndcat(self, **kwargs):
        # Cross-match secondary catalogue with a randomized
        # version of the primary catalogue
        original_pcat = deepcopy(self.pcat)
        self.pcat = self.pcat.randomise(numrepeat=1)

        # Hide std ouput of lr match
        with redirect_stdout(open(os.devnull, "w")):
            mcat_pidx, mcat_sidx, mcat_d2d = self._candidates()

            lr, _ = self._likelihood_ratio(mcat_pidx, mcat_sidx, mcat_d2d)

            # The value for prob_ratio_secondary doesn't matter here,
            # because secondary matches are not used for the statistics
            match_rnd = self._final_table(lr, prob_ratio_secondary=0.5)

        # Recover original pcat
        self.pcat = original_pcat

        return match_rnd

    def _match_fake(self, candidates, **kwargs):
        # Cross-match a randomized version of the primary catalogue
        # with a secondary catalogue where fake counterparts for the primary
        # have been introduced. This is for calculating statistics using
        # the Broos et al. 2006 method.
        original_pcat = deepcopy(self.pcat)
        original_scat = deepcopy(self.scat)

        self.pcat = self.pcat.randomise(numrepeat=1)

        # Create a set of fake counterparts for the primary catalogue
        fakes = self.pcat.set_fake_counterparts(candidates)

        # Remove candidates from the secondary catalogue
        scat_nocandidates = self.scat.remove_by_id(candidates.ids)

        # Set as secondary catalogue the union of the candidates-removed
        # secondary catalogue with the catalogue of fake counterparts
        self.scats[0] = scat_nocandidates.join(fakes)

        # Hide std ouput of lr match
        with redirect_stdout(open(os.devnull, "w")):
            mcat_pidx, mcat_sidx, mcat_d2d = self._candidates()

            lr, _ = self._likelihood_ratio(mcat_pidx, mcat_sidx, mcat_d2d)

            # The value for prob_ratio_secondary doesn't matter here,
            # because secondary matches are not used for the statistics
            match_rnd = self._final_table(lr, prob_ratio_secondary=0.5)

        # Recover original catalogues
        self.pcat = original_pcat
        self.scats[0] = original_scat

        return match_rnd
