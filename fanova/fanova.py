import numpy as np
from collections import OrderedDict
import itertools as it
import pyrfr.regression as reg
import pyrfr.util
import ConfigSpace
from ConfigSpace.hyperparameters import CategoricalHyperparameter, UniformFloatHyperparameter


class fANOVA(object):
    def __init__(self, X=None, Y=None, cs=None, forest=None, 
                num_trees=16, seed=None, bootstrapping=True,
                points_per_tree = None, max_features=None,
                min_samples_split=0, min_samples_leaf=0,
                max_depth=64):

        """
        Calculate and provide midpoints and sizes from the forest's 
        split values in order to get the marginals
        
        Parameters
        ------------
        X: matrix with the features
        
        Y: vector with the response values
        
        cs : ConfigSpace instantiation
        
        forest: trained random forest

        num_trees: number of trees in the forest to be fit
        
        seed: seed for the forests randomness
        
        bootstrapping: whether or not to bootstrap the data for each tree
        
        points_per_tree: number of points used for each tree 
                        (only subsampling if bootstrapping is false)
        
        max_features: number of features to be used at each split, default is 70%
        
        min_samples_split: minimum number of samples required to attempt to split 
        
        min_samples_leaf: minimum number of samples required in a leaf
        
        max_depth: maximal depth of each tree in the forest
        """

        self.num_trees = num_trees
        # if no ConfigSpace is specified, let's build one with all continuous variables
        if (cs is None):
            if (X is None) or (Y is None):
                raise RuntimeError("If no ConfigSpace argument is given, you have to "
                                    "provide data for X and Y.")


            # if no info is given, use min and max values of each variable as bounds
            pcs = list(zip( np.min(X,axis=0), np.max(X, axis=0) ))
            cs = ConfigSpace.ConfigurationSpace()
            for i in range(len(pcs)):
                cs.add_hyperparameter(UniformFloatHyperparameter("%i" %i, pcs[i][0], pcs[i][1]))

        self.cs = cs        
        self.cs_params =self.cs.get_hyperparameters()
        # at this point we have a valid ConfigSpace object
        # check if param number is correct etc:
        if X.shape[1] != len(self.cs_params):
            raise RuntimeError('Number of parameters in config space do not match input X')
        for i in range(len(self.cs_params)):
            if not isinstance(self.cs_params[i], (CategoricalHyperparameter)):
                if (np.max(X[:,i]) > self.cs_params[i].upper) or (np.min(X[:,i]) > self.cs_params[i].lower):
                    raise RuntimeError('Some sample values from X are not in the given interval')
            else:
                unique_vals = set(X[:,i])
                if len(unique_vals) > self.cs_params[i]._num_choices:
                    raise RuntimeError('There are some categoricals missing in the config space specification')
                if len(unique_vals) < self.cs_params[i]._num_choices:
                    raise RuntimeError('There are too many categoricals specified in the config space')



        # initialize all types as 0
        types = np.zeros(len(self.cs_params), dtype=np.uint)
        # retrieve the types from the ConfigSpace 
        # TODO: Test if that actually works
        for i, hp in enumerate(self.cs_params):
            if isinstance( hp , CategoricalHyperparameter):
                types[i] = len(hp.choices)

        
        # train a random forest if none is provided
        if (forest is None):
            if (X is None) or (Y is None):
                raise RuntimeError("If no pyrfr forest is present, you have to "
                                    "provide data for X and Y.")

            forest = reg.fanova_forest()
            forest.options.num_trees = num_trees
            forest.options.seed = np.random.randint(2**31-1) if seed is None else seed
            forest.options.do_bootstrapping = bootstrapping
            forest.options.num_data_points_per_tree = X.shape[0] if points_per_tree is None else points_per_tree
            forest.options.max_features = (X.shape[1]*7)//10 if max_features is None else max_features

            #forest.min_samples_to_split = min_samples_split
            #forest.min_samples_in_leaf = min_samples_leaf
            #forest.max_depth=max_depth
            #forest.epsilon_purity = 1e-8

            rng = reg.default_random_engine()
            data = reg.data_container()
            for i in range(len(Y)):
                data.add_data_point(X[i],Y[i])
            forest.fit(data, rng)
        """soon
        # 
        else:
            assert( types == forest.get_types())
        """

        self.the_forest = forest

        # initialize a dictionary with parameter dims
        self.param_dic = OrderedDict([('parameters', OrderedDict([]))])       


        # getting split values
        forest_split_values = self.the_forest.all_split_values(types)
        
        
        self.all_midpoints = []
        self.all_sizes = []
        
        # set the max and min of values and store them
        val_mins = []
        val_maxs = []
        for param in self.cs_params:
            if isinstance(param, (CategoricalHyperparameter)):
                val_mins.append(None)
                val_maxs.append(None)
            else:
                val_mins.append(param.lower)
                val_maxs.append(param.upper)
            
        for tree_split_values in forest_split_values:
            # considering the hyperparam settings
            var_splits = []
            # categoricals are treated differently        
            for i in range(len(tree_split_values)):
                if val_mins[i] is None:
                    var_splits.append(tree_split_values[i])
                else:
                    plus_setting = [val_mins[i]] + tree_split_values[i] + [val_maxs[i]]
                    var_splits.append(plus_setting)
                    
    
            sizes =[]
            midpoints =  []
            for i, var_splits in enumerate(tree_split_values):
                if val_mins[i] is None: # categorical parameter
                    midpoint_p = var_splits
                    size = np.ones(len(midpoint_p))
                    midpoints.append(midpoint_p)
                    sizes.append(size)
                else:
                    # compute the midpoints
                    midpoint_p = (1/2)* (np.array(var_splits[1:]) + np.array(var_splits[:-1]))
                    size = np.array(var_splits[1:]) - np.array(var_splits[:-1])
                    midpoints.append(midpoint_p)
                    sizes.append(size)

            # all midpoints treewise for the whole forest
            self.all_midpoints.append(midpoints)
            self.all_sizes.append(sizes)
        

    def get_marginal(self, dim_list, outputdict=False):
        """
        Returns the marginal of selected parameters
                
        Parameters
        ----------
        dim_list: list
                Contains the indices of ConfigSpace for the selected parameters 
                (starts with 0) 
        
        outputdict : boolean
                    returns the whole dictionary with all previously 
                    calculated marginals
        Returns
        -------
        default : double
                marginal value
        """        
        
        K = len(dim_list)
        for k in range(1,K+1):
            for dimensions in tuple(it.combinations(dim_list, k)):
				# check if the value has been computed previously
                if self.param_dic['parameters'].has_key(dimensions):
                    thisMarginalVarianceContribution = self.param_dic['parameters'][dimensions]['MarginalVarianceContribution']
                else:
                    for tree in range(len(self.all_midpoints)):
                        sample = np.ones(len(self.all_midpoints[tree]), dtype=np.float)
                        combi_midpoints = []
                        sizes = []
                        dim_helper = []
                        for dim in dimensions:
                            combi_midpoints.append(self.all_midpoints[tree][dim])
                            sizes.append(self.all_sizes[tree][dim])
                            dim_helper.append(dim)
                        midpoints = list(it.product(*combi_midpoints))
                        interval_sizes = list(it.product(*sizes))
                        sample[:] = np.nan
                        weightedSum = 0
                        weightedSumOfSquares = 0
                        prev_FraqExp = 0
                        for i, points in enumerate(midpoints):
                            singleVarianceContributions = []
                            for j in range(len(points)):
                                sample[dim_helper[j]] = points[j]
   
                            pred = self.the_forest.marginal_mean_prediction(sample)
                            marg = pred[tree]
                            
                            w_stats = pyrfr.util.weighted_running_stats()
                            if len(dimensions)== 1:
                                # weightedSum += marg*self.all_sizes[tree][dim][i]
                                # weightedSumOfSquares += np.power(marg,2)*self.all_sizes[tree][dim][i]
                                weightedSumOfSquares += w_stats.push(marg, self.all_sizes[tree][dim][i]).variance_unbiased_importance()
                                thisMarginalVarianceContribution = weightedSumOfSquares - np.power(weightedSum,2)
                                # store into dictionary as one param
                                self.param_dic['parameters'][dimensions] = {}
                                self.param_dic['parameters'][dimensions]['Name'] = self.cs_params[dim].name
                                self.param_dic['parameters'][dimensions]['MarginalVarianceContribution'] = thisMarginalVarianceContribution 
                            else:

                                weightedSum += marg*np.prod(np.array(interval_sizes[i]))
                                weightedSumOfSquares += np.power(marg,2)*np.prod(np.array(interval_sizes[i]))
                                thisMarginalVarianceContribution = weightedSumOfSquares - np.power(weightedSum,2)
                                
                                if len(dimensions) > 2:
                                    singleVar_dims = tuple(it.combinations(dim_list, k-1))
                                    #for single_Var_dims in singleVar_tuples
                                    for i in range(len(points)):
                                        singleVarianceContributions.append(self.param_dic['parameters'][singleVar_dims[i]]['MarginalVarianceContribution'])
                                    for singleVarianceContribution in singleVarianceContributions:
                                        thisMarginalVarianceContribution -= singleVarianceContribution
                                else:
                                    
                                    for i in range(len(points)):
                                        singleVarianceContributions.append(self.param_dic['parameters'][(dim_helper[i], )]['MarginalVarianceContribution'])
                                    for singleVarianceContribution in singleVarianceContributions:
                                        thisMarginalVarianceContribution -= singleVarianceContribution
                                params = tuple(dim_helper)
                                
                                # ToDO computeTotalVarianceOfRegressionTree treewise
                                totalFractionsExplained = prev_FraqExp + 1/(self.num_trees*(thisMarginalVarianceContribution/thisTreeTotalVar*100))
                                prev_FraqExp = self.num_trees*(thisMarginalVarianceContribution/thisTreeTotalVar*100)
                                # store it into dictionary as tuple
                                self.param_dic['parameters'][params] = {}
                                self.param_dic['parameters'][params]['MarginalVarianceContribution'] = totalFractionsExplained
            return self.param_dict
        else:
            return totalFractionsExplained

        
    def get_marginal_for_values(self, dimlist, valuesToPredict):
        """
        Returns the marginal of selected parameters for specific values
                
        Parameters
        ----------
        dimlist: list
                Contains the indices of ConfigSpace for the selected parameters 
                (starts with 0) 
        
        valuesToPredict: list
                Contains the values to be predicted
              
        Returns
        -------
        double
            marginal value
        """
        num_dims = len(self.all_midpoints[0])
        sample = np.empty(num_dims, dtype=np.float)
        sample.fill(np.NAN)
        for i in range(len(dimlist)):
            sample[dimlist[i]] = valuesToPredict[i]
        preds = self.the_forest.marginal_mean_prediction(sample)
    
        return np.mean(preds), np.std(preds)

    def get_most_important_pairwise_marginals(self, n=10):
        """
        Returns the n most important pairwise marginals from the whole ConfigSpace
            
        Parameters
        ----------
        n: int
             The number of most relevant pairwise marginals that will be returned
          
        Returns
        -------
        list: 
             Contains the n most important pairwise marginals
        """
        pairwise_marginals = []
        dimensions = range(len(self.cs_params))
        for combi in it.combinations(dimensions,2):
            pairwise_marginal_performance = self.get_marginal(combi)
            pairwise_marginals.append((pairwise_marginal_performance, combi[0], combi[1]))
        
        pairwise_marginal_performance = sorted(pairwise_marginals, reverse=True)
        important_pairwise_marginals = [(p1, p2) for marginal, p1, p2  in pairwise_marginal_performance[:n]]

        return important_pairwise_marginals
