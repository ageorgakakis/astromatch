"""
astromatch module for cross-matching astronomical catalogues
using the Nway python package. Nway by Johannes Buchnner.

Reference: Salvato et al. 2018.

@author: A.Ruiz
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from six.moves import range
from io import open

import os

try:
    # python 3
    from contextlib import redirect_stdout
except:
    # python 2
    from contextlib2 import redirect_stdout

try:
    # python 3
    FileNotFoundError
except NameError:
    # python 2
    FileNotFoundError = IOError

from astropy import units as u
from astropy.table import Table, join
from nwaylib import nway_match

from .priors import Prior
from .match import BaseMatch


class NWMatch(BaseMatch):
    """
    Class for crossmatching catalogues using Nway.
    """

    _cutoff_column = 'prob_has_match'

    ### Public Methods
    def run(self, radius=6*u.arcsec, use_mags=False, bayes_prior=True, **kwargs):

        self.radius = radius
        mag_radius = kwargs.get('mag_include_radius', 6)
        nwaycats_list, priors_dict = self._get_nwaycats(mag_radius, use_mags, 
                                                        bayes_prior)

        # We need to store the magnitude histogram files generated by nway
        # when bayes_prior is used, in order to load them later.
        if use_mags:
            if len(priors_dict):
                store_mag_hists = kwargs.get('store_mag_hists', False)
            else:            
                store_mag_hists = True
        else:
            store_mag_hists = False
        user_store_mag_hists = kwargs.pop('store_mag_hists', False)

        match = self._nway_match(nwaycats_list, store_mag_hists, **kwargs)
        
        if use_mags:
            # define _priors attribute
            if not len(priors_dict):
                self._priors = self._prior_from_mag_hist_files()
            else:            
                self._priors = priors_dict

            if not user_store_mag_hists:
                self._remove_mag_hist_files()

        return match        

    ### Internal Methods
    def _get_nwaycats(self, mag_radius, use_mags, bayes_prior):
        priors_dict = {}
        nwaycats_list = [self.pcat.nway_dict()]
        for cat in self.scats:
            nwaycat = cat.nway_dict(use_mags=use_mags)

            if use_mags and not bayes_prior:
                prior = Prior(self.pcat, cat, radius=mag_radius*u.arcsec)
                nwaycat['maghists'] = prior.to_nway_hists()
                priors_dict[cat.name] = prior

            nwaycats_list.append(nwaycat)
            
        return nwaycats_list, priors_dict

    def _nway_match(self, nwaycats_list, store_mag_hists, **kwargs):
        match_radius = self.radius.to(u.arcsec).value

        match = nway_match(nwaycats_list, match_radius=match_radius, 
                           store_mag_hists=store_mag_hists, **kwargs)

        match = self._nway_add_srcids(match)
        
        return match
        
    def _nway_add_srcids(self, match):
        
        match = Table.from_pandas(match)
        match['IDX'] = range(len(match))

        catalogues = [self.pcat] + list(self.scats)        
        for cat in catalogues[::-1]:
            srcids = Table()            
            srcids['SRCID_' + cat.name] = cat.ids.data
            srcids[cat.name] = range(len(cat))
            match = join(srcids, match, keys=cat.name, join_type='right')
            match.remove_column(cat.name)
        
        # Recover original sorting
        match.sort('IDX')
        match.remove_column('IDX')
        #match.remove_column([cat.name for cat in catalogues])

        return match

    def _prior_from_mag_hist_files(self):
        priors_dict = {}
        for cat in self.scats:
            name = cat.name
            priors_dict[name] = Prior.from_nway_hists(name, cat.mags.colnames)
            
        return priors_dict

    def _remove_mag_hist_files(self, path='.'):
        for cat in self.scats:
            name = cat.name
            for mag in cat.mags.colnames:
                histfile = os.path.join(path, '{}_{}_fit.txt'.format(name, mag))
                plotfile = os.path.join(path, '{}_{}_fit.pdf'.format(name, mag))

                try:
                    os.remove(histfile)
                    os.remove(plotfile)
                except (FileNotFoundError, OSError):
                    pass

    def _match_rndcat(self, **kwargs):
        # Create a new list of catalogues in nway format, including the 
        # random catalogue as primary and the already defined priors
        rndcat = self.pcat.randomise(numrepeat=1)
        
        nwaycats_list = [rndcat.nway_dict()]
        for cat in self.scats:
            nwaycat = cat.nway_dict(use_mags=False)
            
            if self._priors is not None:
                prior = self._priors[cat.name]
                nwaycat['maghists'] = prior.to_nway_hists()

            nwaycats_list.append(nwaycat)

        # Hide std ouput of nway_match
        with redirect_stdout(open(os.devnull, "w")): 
            match_radius = self.radius.to(u.arcsec).value
            match_rnd = nway_match(nwaycats_list, match_radius=match_radius, 
                                   store_mag_hists=False, **kwargs)            

        return Table.from_pandas(match_rnd)

    @staticmethod
    def _reliability(data):
        return data['prob_has_match']*data['prob_this_match']