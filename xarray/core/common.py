from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import numpy as np
import pandas as pd

from .pycompat import (basestring, suppress, dask_array_type,
                       OrderedDict)
from . import formatting
from .utils import SortedKeysDict, not_implemented, Frozen


class ImplementsArrayReduce(object):
    @classmethod
    def _reduce_method(cls, func, include_skipna, numeric_only):
        if include_skipna:
            def wrapped_func(self, dim=None, axis=None, skipna=None,
                             keep_attrs=False, **kwargs):
                return self.reduce(func, dim, axis, keep_attrs=keep_attrs,
                                   skipna=skipna, allow_lazy=True, **kwargs)
        else:
            def wrapped_func(self, dim=None, axis=None, keep_attrs=False,
                             **kwargs):
                return self.reduce(func, dim, axis, keep_attrs=keep_attrs,
                                   allow_lazy=True, **kwargs)
        return wrapped_func

    _reduce_extra_args_docstring = \
        """dim : str or sequence of str, optional
            Dimension(s) over which to apply `{name}`.
        axis : int or sequence of int, optional
            Axis(es) over which to apply `{name}`. Only one of the 'dim'
            and 'axis' arguments can be supplied. If neither are supplied, then
            `{name}` is calculated over axes."""

    _cum_extra_args_docstring = \
        """dim : str or sequence of str, optional
            Dimension over which to apply `{name}`.
        axis : int or sequence of int, optional
            Axis over which to apply `{name}`. Only one of the 'dim'
            and 'axis' arguments can be supplied."""


class ImplementsDatasetReduce(object):
    @classmethod
    def _reduce_method(cls, func, include_skipna, numeric_only):
        if include_skipna:
            def wrapped_func(self, dim=None, keep_attrs=False, skipna=None,
                             **kwargs):
                return self.reduce(func, dim, keep_attrs, skipna=skipna,
                                   numeric_only=numeric_only, allow_lazy=True,
                                   **kwargs)
        else:
            def wrapped_func(self, dim=None, keep_attrs=False, **kwargs):
                return self.reduce(func, dim, keep_attrs,
                                   numeric_only=numeric_only, allow_lazy=True,
                                   **kwargs)
        return wrapped_func

    _reduce_extra_args_docstring = \
        """dim : str or sequence of str, optional
            Dimension(s) over which to apply `func`.  By default `func` is
            applied over all dimensions."""

    _cum_extra_args_docstring = \
        """dim : str or sequence of str, optional
            Dimension over which to apply `{name}`.
        axis : int or sequence of int, optional
            Axis over which to apply `{name}`. Only one of the 'dim'
            and 'axis' arguments can be supplied."""


class ImplementsRollingArrayReduce(object):
    @classmethod
    def _reduce_method(cls, func):
        def wrapped_func(self, **kwargs):
            return self.reduce(func, **kwargs)
        return wrapped_func

    @classmethod
    def _bottleneck_reduce(cls, func):
        def wrapped_func(self, **kwargs):
            from .dataarray import DataArray

            if isinstance(self.obj.data, dask_array_type):
                raise NotImplementedError(
                    'Rolling window operation does not work with dask arrays')

            # bottleneck doesn't allow min_count to be 0, although it should
            # work the same as if min_count = 1
            if self.min_periods is not None and self.min_periods == 0:
                min_count = self.min_periods + 1
            else:
                min_count = self.min_periods

            values = func(self.obj.data, window=self.window,
                          min_count=min_count, axis=self._axis_num)

            result = DataArray(values, self.obj.coords)

            if self.center:
                result = self._center_result(result)

            return result
        return wrapped_func

    @classmethod
    def _bottleneck_reduce_without_min_count(cls, func):
        def wrapped_func(self, **kwargs):
            from .dataarray import DataArray

            if self.min_periods is not None:
                raise ValueError('Rolling.median does not accept min_periods')

            if isinstance(self.obj.data, dask_array_type):
                raise NotImplementedError(
                    'Rolling window operation does not work with dask arrays')

            values = func(self.obj.data, window=self.window, axis=self._axis_num)

            result = DataArray(values, self.obj.coords)

            if self.center:
                result = self._center_result(result)

            return result
        return wrapped_func


class AbstractArray(ImplementsArrayReduce, formatting.ReprMixin):
    """Shared base class for DataArray and Variable."""

    def __bool__(self):
        return bool(self.values)

    # Python 3 uses __bool__, Python 2 uses __nonzero__
    __nonzero__ = __bool__

    def __float__(self):
        return float(self.values)

    def __int__(self):
        return int(self.values)

    def __complex__(self):
        return complex(self.values)

    def __long__(self):
        return long(self.values)

    def __array__(self, dtype=None):
        return np.asarray(self.values, dtype=dtype)

    def __repr__(self):
        return formatting.array_repr(self)

    def _iter(self):
        for n in range(len(self)):
            yield self[n]

    def __iter__(self):
        if self.ndim == 0:
            raise TypeError('iteration over a 0-d array')
        return self._iter()

    @property
    def T(self):
        return self.transpose()

    def get_axis_num(self, dim):
        """Return axis number(s) corresponding to dimension(s) in this array.

        Parameters
        ----------
        dim : str or iterable of str
            Dimension name(s) for which to lookup axes.

        Returns
        -------
        int or tuple of int
            Axis number or numbers corresponding to the given dimensions.
        """
        if isinstance(dim, basestring):
            return self._get_axis_num(dim)
        else:
            return tuple(self._get_axis_num(d) for d in dim)

    def _get_axis_num(self, dim):
        try:
            return self.dims.index(dim)
        except ValueError:
            raise ValueError("%r not found in array dimensions %r" %
                             (dim, self.dims))

    @property
    def sizes(self):
        """Ordered mapping from dimension names to lengths.

        Immutable.

        See also
        --------
        Dataset.sizes
        """
        return Frozen(OrderedDict(zip(self.dims, self.shape)))


class AttrAccessMixin(object):
    """Mixin class that allows getting keys with attribute access
    """
    _initialized = False

    @property
    def _attr_sources(self):
        """List of places to look-up items for attribute-style access"""
        return []

    def __getattr__(self, name):
        if name != '__setstate__':
            # this avoids an infinite loop when pickle looks for the
            # __setstate__ attribute before the xarray object is initialized
            for source in self._attr_sources:
                with suppress(KeyError):
                    return source[name]
        raise AttributeError("%r object has no attribute %r" %
                             (type(self).__name__, name))

    def __setattr__(self, name, value):
        if self._initialized:
            try:
                # Allow setting instance variables if they already exist
                # (e.g., _attrs). We use __getattribute__ instead of hasattr
                # to avoid key lookups with attribute-style access.
                self.__getattribute__(name)
            except AttributeError:
                raise AttributeError(
                    "cannot set attribute %r on a %r object. Use __setitem__ "
                    "style assignment (e.g., `ds['name'] = ...`) instead to "
                    "assign variables." % (name, type(self).__name__))
        object.__setattr__(self, name, value)

    def __dir__(self):
        """Provide method name lookup and completion. Only provide 'public'
        methods.
        """
        extra_attrs = [
            item for sublist in self._attr_sources for item in sublist
            if isinstance(item, basestring)]
        return sorted(set(dir(type(self)) + extra_attrs))


def get_squeeze_dims(xarray_obj, dim):
    """Get a list of dimensions to squeeze out.
    """
    if dim is None:
        dim = [d for d, s in xarray_obj.sizes.items() if s == 1]
    else:
        if isinstance(dim, basestring):
            dim = [dim]
        if any(xarray_obj.sizes[k] > 1 for k in dim):
            raise ValueError('cannot select a dimension to squeeze out '
                             'which has length greater than one')
    return dim


class BaseDataObject(AttrAccessMixin):
    """Shared base class for Dataset and DataArray."""

    def squeeze(self, dim=None, drop=False):
        """Return a new object with squeezed data.

        Parameters
        ----------
        dim : None or str or tuple of str, optional
            Selects a subset of the length one dimensions. If a dimension is
            selected with length greater than one, an error is raised. If
            None, all length one dimensions are squeezed.
        drop : bool, optional
            If ``drop=True``, drop squeezed coordinates instead of making them
            scalar.

        Returns
        -------
        squeezed : same type as caller
            This object, but with with all or a subset of the dimensions of
            length 1 removed.

        See Also
        --------
        numpy.squeeze
        """
        dims = get_squeeze_dims(self, dim)
        return self.isel(drop=drop, **{d: 0 for d in dims})

    def get_index(self, key):
        """Get an index for a dimension, with fall-back to a default RangeIndex
        """
        if key not in self.dims:
            raise KeyError(key)

        try:
            return self.indexes[key]
        except KeyError:
            return pd.Index(range(self.sizes[key]), name=key)

    def _calc_assign_results(self, kwargs):
        results = SortedKeysDict()
        for k, v in kwargs.items():
            if callable(v):
                results[k] = v(self)
            else:
                results[k] = v
        return results

    def assign_coords(self, **kwargs):
        """Assign new coordinates to this object, returning a new object
        with all the original data in addition to the new coordinates.

        Parameters
        ----------
        kwargs : keyword, value pairs
            keywords are the variables names. If the values are callable, they
            are computed on this object and assigned to new coordinate
            variables. If the values are not callable, (e.g. a DataArray,
            scalar, or array), they are simply assigned.

        Returns
        -------
        assigned : same type as caller
            A new object with the new coordinates in addition to the existing
            data.

        Notes
        -----
        Since ``kwargs`` is a dictionary, the order of your arguments may not
        be preserved, and so the order of the new variables is not well
        defined. Assigning multiple variables within the same ``assign_coords``
        is possible, but you cannot reference other variables created within
        the same ``assign_coords`` call.

        See also
        --------
        Dataset.assign
        """
        data = self.copy(deep=False)
        results = self._calc_assign_results(kwargs)
        data.coords.update(results)
        return data

    def pipe(self, func, *args, **kwargs):
        """
        Apply func(self, *args, **kwargs)

        This method replicates the pandas method of the same name.

        Parameters
        ----------
        func : function
            function to apply to this xarray object (Dataset/DataArray).
            ``args``, and ``kwargs`` are passed into ``func``.
            Alternatively a ``(callable, data_keyword)`` tuple where
            ``data_keyword`` is a string indicating the keyword of
            ``callable`` that expects the xarray object.
        args : positional arguments passed into ``func``.
        kwargs : a dictionary of keyword arguments passed into ``func``.

        Returns
        -------
        object : the return type of ``func``.

        Notes
        -----

        Use ``.pipe`` when chaining together functions that expect
        xarray or pandas objects, e.g., instead of writing

        >>> f(g(h(ds), arg1=a), arg2=b, arg3=c)

        You can write

        >>> (ds.pipe(h)
        ...    .pipe(g, arg1=a)
        ...    .pipe(f, arg2=b, arg3=c)
        ... )

        If you have a function that takes the data as (say) the second
        argument, pass a tuple indicating which keyword expects the
        data. For example, suppose ``f`` takes its data as ``arg2``:

        >>> (ds.pipe(h)
        ...    .pipe(g, arg1=a)
        ...    .pipe((f, 'arg2'), arg1=a, arg3=c)
        ...  )

        See Also
        --------
        pandas.DataFrame.pipe
        """
        if isinstance(func, tuple):
            func, target = func
            if target in kwargs:
                msg = '%s is both the pipe target and a keyword argument' % target
                raise ValueError(msg)
            kwargs[target] = self
            return func(*args, **kwargs)
        else:
            return func(self, *args, **kwargs)

    def groupby(self, group, squeeze=True):
        """Returns a GroupBy object for performing grouped operations.

        Parameters
        ----------
        group : str, DataArray or IndexVariable
            Array whose unique values should be used to group this array. If a
            string, must be the name of a variable contained in this dataset.
        squeeze : boolean, optional
            If "group" is a dimension of any arrays in this dataset, `squeeze`
            controls whether the subarrays have a dimension of length 1 along
            that dimension or if the dimension is squeezed out.

        Returns
        -------
        grouped : GroupBy
            A `GroupBy` object patterned after `pandas.GroupBy` that can be
            iterated over in the form of `(unique_value, grouped_array)` pairs.
        """
        return self.groupby_cls(self, group, squeeze=squeeze)

    def groupby_bins(self, group, bins, right=True, labels=None, precision=3,
                     include_lowest=False, squeeze=True):
        """Returns a GroupBy object for performing grouped operations.

        Rather than using all unique values of `group`, the values are discretized
        first by applying `pandas.cut` [1]_ to `group`.

        Parameters
        ----------
        group : str, DataArray or IndexVariable
            Array whose binned values should be used to group this array. If a
            string, must be the name of a variable contained in this dataset.
        bins : int or array of scalars
            If bins is an int, it defines the number of equal-width bins in the
            range of x. However, in this case, the range of x is extended by .1%
            on each side to include the min or max values of x. If bins is a
            sequence it defines the bin edges allowing for non-uniform bin
            width. No extension of the range of x is done in this case.
        right : boolean, optional
            Indicates whether the bins include the rightmost edge or not. If
            right == True (the default), then the bins [1,2,3,4] indicate
            (1,2], (2,3], (3,4].
        labels : array or boolean, default None
            Used as labels for the resulting bins. Must be of the same length as
            the resulting bins. If False, string bin labels are assigned by
            `pandas.cut`.
        precision : int
            The precision at which to store and display the bins labels.
        include_lowest : bool
            Whether the first interval should be left-inclusive or not.
        squeeze : boolean, optional
            If "group" is a dimension of any arrays in this dataset, `squeeze`
            controls whether the subarrays have a dimension of length 1 along
            that dimension or if the dimension is squeezed out.

        Returns
        -------
        grouped : GroupBy
            A `GroupBy` object patterned after `pandas.GroupBy` that can be
            iterated over in the form of `(unique_value, grouped_array)` pairs.
            The name of the group has the added suffix `_bins` in order to
            distinguish it from the original variable.

        References
        ----------
        .. [1] http://pandas.pydata.org/pandas-docs/stable/generated/pandas.cut.html
        """
        return self.groupby_cls(self, group, squeeze=squeeze, bins=bins,
                                cut_kwargs={'right': right, 'labels': labels,
                                            'precision': precision,
                                            'include_lowest': include_lowest})

    def rolling(self, min_periods=None, center=False, **windows):
        """
        Rolling window object.

        Rolling window aggregations are much faster when bottleneck is
        installed.

        Parameters
        ----------
        min_periods : int, default None
            Minimum number of observations in window required to have a value
            (otherwise result is NA). The default, None, is equivalent to
            setting min_periods equal to the size of the window.
        center : boolean, default False
            Set the labels at the center of the window.
        **windows : dim=window
            dim : str
                Name of the dimension to create the rolling iterator
                along (e.g., `time`).
            window : int
                Size of the moving window.

        Returns
        -------
        rolling : type of input argument
        """

        return self.rolling_cls(self, min_periods=min_periods,
                                center=center, **windows)

    def resample(self, freq, dim, how='mean', skipna=None, closed=None,
                 label=None, base=0, keep_attrs=False):
        """Resample this object to a new temporal resolution.

        Handles both downsampling and upsampling. Upsampling with filling is
        not yet supported; if any intervals contain no values in the original
        object, they will be given the value ``NaN``.

        Parameters
        ----------
        freq : str
            String in the '#offset' to specify the step-size along the
            resampled dimension, where '#' is an (optional) integer multipler
            (default 1) and 'offset' is any pandas date offset alias. Examples
            of valid offsets include:

            * 'AS': year start
            * 'QS-DEC': quarterly, starting on December 1
            * 'MS': month start
            * 'D': day
            * 'H': hour
            * 'Min': minute

            The full list of these offset aliases is documented in pandas [1]_.
        dim : str
            Name of the dimension to resample along (e.g., 'time').
        how : str or func, optional
            Used for downsampling. If a string, ``how`` must be a valid
            aggregation operation supported by xarray. Otherwise, ``how`` must be
            a function that can be called like ``how(values, axis)`` to reduce
            ndarray values along the given axis. Valid choices that can be
            provided as a string include all the usual Dataset/DataArray
            aggregations (``all``, ``any``, ``argmax``, ``argmin``, ``max``,
            ``mean``, ``median``, ``min``, ``prod``, ``sum``, ``std`` and
            ``var``), as well as ``first`` and ``last``.
        skipna : bool, optional
            Whether to skip missing values when aggregating in downsampling.
        closed : 'left' or 'right', optional
            Side of each interval to treat as closed.
        label : 'left or 'right', optional
            Side of each interval to use for labeling.
        base : int, optionalt
            For frequencies that evenly subdivide 1 day, the "origin" of the
            aggregated intervals. For example, for '24H' frequency, base could
            range from 0 through 23.
        keep_attrs : bool, optional
            If True, the object's attributes (`attrs`) will be copied from
            the original object to the new one.  If False (default), the new
            object will be returned without attributes.

        Returns
        -------
        resampled : same type as caller
            This object resampled.

        References
        ----------

        .. [1] http://pandas.pydata.org/pandas-docs/stable/timeseries.html#offset-aliases
        """
        from .dataarray import DataArray

        RESAMPLE_DIM = '__resample_dim__'
        if isinstance(dim, basestring):
            dim = self[dim]
        group = DataArray(dim, [(RESAMPLE_DIM, dim)], name=RESAMPLE_DIM)
        time_grouper = pd.TimeGrouper(freq=freq, how=how, closed=closed,
                                      label=label, base=base)
        gb = self.groupby_cls(self, group, grouper=time_grouper)
        if isinstance(how, basestring):
            f = getattr(gb, how)
            if how in ['first', 'last']:
                result = f(skipna=skipna, keep_attrs=keep_attrs)
            else:
                result = f(dim=dim.name, skipna=skipna, keep_attrs=keep_attrs)
        else:
            result = gb.reduce(how, dim=dim.name, keep_attrs=keep_attrs)
        result = result.rename({RESAMPLE_DIM: dim.name})
        return result

    def where(self, cond, other=None, drop=False):
        """Return an object of the same shape with all entries where cond is
        True and all other entries masked.

        This operation follows the normal broadcasting and alignment rules that
        xarray uses for binary arithmetic.

        Parameters
        ----------
        cond : boolean DataArray or Dataset
        other : unimplemented, optional
            Unimplemented placeholder for compatibility with future
            numpy / pandas versions
        drop : boolean, optional
            Coordinate labels that only correspond to NA values should be dropped

        Returns
        -------
        same type as caller or if drop=True same type as caller with dimensions
        reduced for dim element where mask is True

        Examples
        --------

        >>> import numpy as np
        >>> a = xr.DataArray(np.arange(25).reshape(5, 5), dims=('x', 'y'))
        >>> a.where((a > 6) & (a < 18))
        <xarray.DataArray (x: 5, y: 5)>
        array([[ nan,  nan,  nan,  nan,  nan],
               [ nan,  nan,   7.,   8.,   9.],
               [ 10.,  11.,  12.,  13.,  14.],
               [ 15.,  16.,  17.,  nan,  nan],
               [ nan,  nan,  nan,  nan,  nan]])
        Coordinates:
          * y        (y) int64 0 1 2 3 4
          * x        (x) int64 0 1 2 3 4
        >>> a.where((a > 6) & (a < 18), drop=True)
        <xarray.DataArray (x: 5, y: 5)>
        array([[ nan,  nan,   7.,   8.,   9.],
               [ 10.,  11.,  12.,  13.,  14.],
               [ 15.,  16.,  17.,  nan,  nan],
        Coordinates:
          * x        (x) int64 1 2 3
          * y        (y) int64 0 1 2 3 4
        """
        if other is not None:
            raise NotImplementedError("The optional argument 'other' has not "
                                      "yet been implemented")

        if drop:
            from .dataarray import DataArray
            from .dataset import Dataset
            # get cond with the minimal size needed for the Dataset
            if isinstance(cond, Dataset):
                clipcond = cond.to_array().any('variable')
            elif isinstance(cond, DataArray):
                clipcond = cond
            else:
                raise TypeError("Cond argument is %r but must be a %r or %r" %
                                (cond, Dataset, DataArray))

            # clip the data corresponding to coordinate dims that are not used
            clip = dict(zip(clipcond.dims, [np.unique(adim)
                                            for adim in np.nonzero(clipcond.values)]))
            outcond = cond.isel(**clip)
            indexers = {dim: outcond.get_index(dim) for dim in outcond.dims}
            outobj = self.sel(**indexers)
        else:
            outobj = self
            outcond = cond

        # preserve attributes
        out = outobj._where(outcond)
        out._copy_attrs_from(self)
        return out

    def close(self):
        """Close any files linked to this object
        """
        if self._file_obj is not None:
            self._file_obj.close()
        self._file_obj = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    # this has no runtime function - these are listed so IDEs know these methods
    # are defined and don't warn on these operations
    __lt__ = __le__ = __ge__ = __gt__ = __add__ = __sub__ = __mul__ = \
        __truediv__ = __floordiv__ = __mod__ = __pow__ = __and__ = __xor__ = \
        __or__ = __div__ = __eq__ = __ne__ = not_implemented


def _maybe_promote(dtype):
    """Simpler equivalent of pandas.core.common._maybe_promote"""
    # N.B. these casting rules should match pandas
    if np.issubdtype(dtype, float):
        fill_value = np.nan
    elif np.issubdtype(dtype, int):
        # convert to floating point so NaN is valid
        dtype = float
        fill_value = np.nan
    elif np.issubdtype(dtype, complex):
        fill_value = np.nan + np.nan * 1j
    elif np.issubdtype(dtype, np.datetime64):
        fill_value = np.datetime64('NaT')
    elif np.issubdtype(dtype, np.timedelta64):
        fill_value = np.timedelta64('NaT')
    else:
        dtype = object
        fill_value = np.nan
    return np.dtype(dtype), fill_value


def _possibly_convert_objects(values):
    """Convert arrays of datetime.datetime and datetime.timedelta objects into
    datetime64 and timedelta64, according to the pandas convention.
    """
    return np.asarray(pd.Series(values.ravel())).reshape(values.shape)


def _get_fill_value(dtype):
    """Return a fill value that appropriately promotes types when used with
    np.concatenate
    """
    _, fill_value = _maybe_promote(dtype)
    return fill_value


def full_like(other, fill_value, dtype=None):
    """Return a new object with the same shape and type as a given object.

    Parameters
    ----------
    other : DataArray, Dataset, or Variable
        The reference object in input
    fill_value : scalar
        Value to fill the new object with before returning it.
    dtype : dtype, optional
        dtype of the new array. If omitted, it defaults to other.dtype.

    Returns
    -------
    out : same as object
        New object with the same shape and type as other, with the data
        filled with fill_value. Coords will be copied from other.
        If other is based on dask, the new one will be as well, and will be
        split in the same chunks.
    """
    from .dataarray import DataArray
    from .dataset import Dataset
    from .variable import Variable

    if isinstance(other, Dataset):
        data_vars = OrderedDict(
            (k, _full_like_variable(v, fill_value, dtype))
            for k, v in other.data_vars.items())
        return Dataset(data_vars, coords=other.coords, attrs=other.attrs)
    elif isinstance(other, DataArray):
        return DataArray(
            _full_like_variable(other.variable, fill_value, dtype),
            dims=other.dims, coords=other.coords, attrs=other.attrs, name=other.name)
    elif isinstance(other, Variable):
        return _full_like_variable(other, fill_value, dtype)
    else:
        raise TypeError("Expected DataArray, Dataset, or Variable")


def _full_like_variable(other, fill_value, dtype=None):
    """Inner function of full_like, where other must be a variable
    """
    from .variable import Variable

    if isinstance(other.data, dask_array_type):
        import dask.array
        if dtype is None:
            dtype = other.dtype
        data = dask.array.full(other.shape, fill_value, dtype=dtype,
                               chunks=other.data.chunks)
    else:
        data = np.full_like(other, fill_value, dtype=dtype)

    return Variable(dims=other.dims, data=data, attrs=other.attrs)


def zeros_like(other, dtype=None):
    """Shorthand for full_like(other, 0, dtype)
    """
    return full_like(other, 0, dtype)


def ones_like(other, dtype=None):
    """Shorthand for full_like(other, 1, dtype)
    """
    return full_like(other, 1, dtype)
