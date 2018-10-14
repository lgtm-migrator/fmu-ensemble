# -*- coding: utf-8 -*-
"""Module for parsing an ensemble from FMU. This class represents an
ensemble, which is nothing but a collection of realizations.

The typical task of this class is book-keeping of each realization,
and abilities to aggregate any information that each realization can
provide.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import yaml
import pandas as pd

from fmu.config import etc
from .realization import ScratchRealization
from .ensemble import ScratchEnsemble
from .virtualrealization import VirtualRealization
from .virtualensemble import VirtualEnsemble

xfmu = etc.Interaction()
logger = xfmu.functionlogger(__name__)


class Observations(object):
    """Represents a set of observations and the ability to
    compare realizations and ensembles to the observations

    The primary data structure is a dictionary holding actual
    observations, this can typically be loaded from a yaml-file

    Key functionality is to be able to compute mismatch pr
    observation and presenting the computed data as a Pandas
    Dataframe. If run on ensembles, every row will be tagged
    by which realization index the data was computed for.

    An observation unit is a concept for the observation and points to
    something we define as a "single" observation. It can be one value
    for one datatype at a specific date, but in the case of Eclipse
    summary vector, it can also be a time-series. Mismatches will
    be computed pr. observation unit.

    The type of observations supported must follow the datatypes that
    the realizations and ensemble objects are able to internalize.
    """

    # Discussion points:
    # * Should mismatch calculation happen in this function
    #   with ensembles/realizations input or the other way around?
    # * Should it be possible to represent the observations
    #   themselves in a dataframe, or does the dict suffice?
    #   (each observation unit should be representable as
    #   a dict, and then it is mergeable in Pandas)

    def __init__(self, observations):
        """Initialize an observation object with observations
        from file or from an incoming dictionary structure

        Observations will be checked for validity, and
        if there are no accepted observations, this will error.

        Args:
            observations: dict with observation structure or string
                with path to a yaml file.
        """
        self.observations = dict()

        if isinstance(observations, str):
            with open(observations) as yamlfile:
                self.observations = yaml.load(yamlfile)
        elif isinstance(observations, dict):
            self.observations = observations
        else:
            raise ValueError("Unsupported object for observations")

        if not self._clean_observations():
            raise ValueError("No usable observations")

    def mismatch(self, ens_or_real):
        """Compute the mismatch from the current observation set
        to the incoming ensemble or realization.

        In the case of an ensemble, it will calculate individually
        for every realization, and aggregate the results.

        Returns:
            dataframe with REAL (only if ensemble), OBSKEY, DATE,
                L1, L2. One row for every observation unit.
        """
        # For ensembles, we should in the future be able to loop
        # over realizations in a multiprocessing fashion
        if isinstance(ens_or_real, VirtualEnsemble) \
           or isinstance(ens_or_real, ScratchEnsemble):
            mismatches = {}
            for realidx, real in ens_or_real._realizations.items():
                mismatches[realidx] = self._realization_mismatch(real)
                mismatches[realidx]['REAL'] = realidx
            return pd.concat(mismatches, axis=0, ignore_index=True)
        elif isinstance(ens_or_real, VirtualRealization) \
             or isinstance(ens_or_real, ScratchRealization):
            return self._realization_mismatch(ens_or_real)
        elif isinstance(ens_or_real, EnsembleSet):
            pass
        else:
            raise ValueError("Unsupported object for mismatch calculation")

    def __len__(self):
        """Return the number of observation units present"""
        # This is not correctly implemented yet..
        return len(self.observations.keys())

    def keys(self):
        """Return a list of observation units present.

        This list might change into a dataframe in the future,
        but calling len() on its results should always return
        the number of observation units."""
        return self.observations.keys()

    def _realization_mismatch(self, real):
        """Compute the mismatch from the current loaded
        observations to a realization.

        Supports both ScratchRealizations and
        VirtualRealizations

        The returned dataframe contains the columns:
            * OBSTYPE - category/type of the observation
            * OBSKEY - name of the observation key
            * DATE - only where relevant.
            * OBSINDEX - where an enumeration is relevant
            * MISMATCH - signed difference between value and result
            * L1 - absolute difference
            * L2 - absolute difference squared
            * SIGN - True if positive difference
        One row for every observation unit.

        Args:
            real : ScratchRealization or VirtualRealization
        Returns:
            dataframe: One row per observation unit with
                mismatch data
        """
        # mismatch_df = pd.DataFrame(columns=['OBSTYPE', 'OBSKEY',
        #     'DATE', 'OBSINDEX', 'MISMATCH', 'L1', 'L2', 'SIGN'])
        mismatches = []
        for obstype in self.observations.keys():
            for obsunit in self.observations[obstype]:  # (list)
                if obstype == 'txt':
                    sim_value = real.get_df(obsunit['localpath'])\
                                [obsunit['key']]
                    mismatch = sim_value - obsunit['value']
                    mismatches.append(dict(OBSTYPE=obstype,
                                           OBSKEY=str(obsunit['localpath']) + '/' + \
                                           str(obsunit['key']),
                                           MISMATCH=mismatch,
                                           L1=abs(mismatch),
                                           L2=abs(mismatch)**2,
                                           SIGN=cmp(mismatch, 0)))
                if obstype == 'scalar':
                    sim_value = real.get_df(obsunit['key'])
                    mismatch = sim_value - obsunit['value']
                    mismatches.append(dict(OBSTYPE=obstype,
                                           OBSKEY=str(obsunit['key']),
                                           MISMATCH=mismatch, L1=abs(mismatch),
                                           L2=abs(mismatch)**2, SIGN=cmp(mismatch, 0)))
                if obstype == 'smryh':
                    # Will use raw times when available.
                    # Time index is always identical
                    sim_hist = real.get_smry(column_keys=[obsunit['key'],
                                                          obsunit['histvec']])
                    sim_hist['mismatch'] = sim_hist[obsunit['key']] - \
                                           sim_hist[obsunit['histvec']]
                    mismatches.append(dict(OBSTYPE='smryh',
                                           OBSKEY=obsunit['key'],
                                           MISMATCH=sim_hist.mismatch.sum(),
                                           L1=sim_hist.mismatch.abs().sum(),
                                           L2=math.sqrt((sim_hist.mismatch ** 2).sum())))
        return pd.DataFrame(mismatches)

    def _realization_misfit(self, real, corr=None):
        """The misfit value for the observation set

        Ref: https://wiki.statoil.no/wiki/indexphp/RP_HM/Observations#Misfit_function

        Args:
            real : a ScratchRealization or a VirtualRealization
            corr : correlation or weigthing matrix (numpy matrix).
                If a list or numpy vector is supplied, it is intepreted
                as a diagonal matrix. If omitted, the identity matrix is used

        Returns:
            float : the misfit value for the observation set and realization
        """
        raise NotImplementedError
        return 1

    def _clean_observations(self):
        """Verify integrity of observations, remove
        observation units that cannot be used.

        Will log warnings about things that are removed.

        Returns number of usable observation units.
        """
        supported_categories = ['smry', 'smryh', 'txt', 'scalar', 'rft']

        # Check top level keys in observations dict:
        for key in self.observations.keys():
            if key not in supported_categories:
                self.observations.pop(key)
                logger.error('Observation category %s not supported',
                             key)
                continue
            if not isinstance(self.observations[key], list):
                logger.error('Observation category %s did not contain a' +
                             'list, but %s',
                             key, type(self.observations[key]))
                self.observations.pop(key)
        if not len(self.observations.keys()):
            logger.error("No parseable observations")
            raise ValueError
        return 1

    def to_ert2observations(self):
        """Convert the observation set to an observation 
        file for use with Ert 2.

        Returns: multiline string
        """
        raise NotImplementedError
        return ""

    def to_yaml(self):
        """Convert the current observations to YAML format

        Returns:
            string : Multiline YAML string.
        """
        return ""
