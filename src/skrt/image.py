"""Classes for loading and comparing medical images."""

from matplotlib.ticker import MultipleLocator, AutoMinorLocator
from pydicom.dataset import FileDataset, FileMetaDataset
import scipy.ndimage
import copy
import datetime
import glob
import functools
import math
import matplotlib as mpl
import matplotlib.cm
import matplotlib.colors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import nibabel
import numpy as np
import os
import pydicom
import tempfile
import time
import uuid
import skimage.transform

import skrt.core


_axes = ["x", "y", "z"]
_slice_axes = {"x-y": 2, "y-z": 0, "x-z": 1}
_plot_axes = {"x-y": [0, 1], "y-z": [2, 1], "x-z": [2, 0]}
_default_figsize = 6
_default_stations = {"0210167": "LA3", "0210292": "LA4"}

# Matplotlib settings
mpl.rcParams["figure.figsize"] = (7.4, 4.8)
mpl.rcParams["font.serif"] = "Times New Roman"
mpl.rcParams["font.family"] = "serif"
mpl.rcParams["font.size"] = 14.0


class Image(skrt.core.Archive):
    """Loads and stores a medical image and its geometrical properties, either
    from a dicom/nifti file or a numpy array."""

    def __init__(
        self,
        path="",
        load=True,
        title=None,
        affine=None,
        voxel_size=(1, 1, 1),
        origin=(0, 0, 0),
        nifti_array=False,
        downsample=None,
    ):
        """
        Initialise from a medical image source.

        Parameters
        ----------
        path : str/array/Nifti1Image, default = ""
            Source of image data. Can be either:
                (a) A string containing the path to a dicom or nifti file;
                (b) A string containing the path to a numpy file containing a
                    2D or 3D array;
                (c) A 2D or 3D numpy array;
                (d) A nibabel.nifti1.Nifti1Image object;
                (e) An existing Image object to be cloned; in this case, all 
                    other input args except <title> will be ignored, as these 
                    will be taken from the existing Image.

        load : bool, default=True
            If True, the image data will be immediately loaded. Otherwise, it
            can be loaded later with the load() method.

        title : str, default=None
            Title to use when plotting the image. If None and <source> is a
            path, a title will be automatically generated from the filename.

        affine : 4x4 array, default=None
            Array containing the affine matrix to use if <source> is a numpy
            array or path to a numpy file. If not None, this takes precendence
            over <voxel_size> and <origin>.

        voxel_size : tuple, default=(1, 1, 1)
            Voxel sizes in mm in order (x, y, z) to use if <source> is a numpy
            array or path to a numpy file and <affine> is not provided.

        origin : tuple, default=(0, 0, 0)
            Origin position in mm in order (x, y, z) to use if <source> is a
            numpy array or path to a numpy file and <affine> is not provided.

        nifti_array : bool, default=False
            If True and <source> is a numpy array or numpy file, the array
            will be treated as a nifti-style array, i.e. (x, y, z) in
            (row, column, slice), as opposed to dicom style.

        downsample : int/list, default=None
            Amount by which to downsample the image. Can be a single value for
            all axes, or a list containing downsampling amounts in order
            (x, y, z).
        """

        # Clone from another Image object
        if issubclass(type(path), Image):
            path.clone_attrs(self)
            if title is not None:
                self.title = title
            return

        # Otherwise, load from source
        self.data = None
        self.title = title
        self.source = path
        self.source_type = None
        self.dicom_dataset = None
        self.voxel_size = list(voxel_size)
        self.origin = list(origin)
        self.affine = affine
        self.downsampling = downsample
        self.nifti_array = nifti_array
        self.structure_sets = []

        path = self.source if isinstance(self.source, str) else ""
        skrt.core.Archive.__init__(self, path)

        if load and (not isinstance(self.source, str) or self.source):
            self.load()

    def get_data(self, standardise=False):
        """Return 3D image array.

        Parameters
        ----------
        standardise : bool, default=False
            If False, the data array will be returned in the orientation in 
            which it was loaded; otherwise, it will be returned in standard
            dicom-style orientation such that [column, row, slice] corresponds
            to the [x, y, z] axes.
        """

        if self.data is None:
            self.load()
        if standardise:
            return self.get_standardised_data(force=True)
        return self.data

    def get_dicom_filepath(self, sl=None, idx=None, pos=None):
        """Return path to the dicom dataset corresponding to a specific 
        slice.

        Parameters
        ----------
        sl : int, default=None
            Slice number; used if not None.

        idx : int, default=None
            Slice array index; used if not None and <sl> is None.

        pos : float, default=None
            Slice position in mm; used if not None and <sl> and <idx> are both
            None.
        """

        if sl is None and idx is None and pos is None:
            print("Must provide a slice number, array index, or position in "
                  "mm!")
            return 

        idx = self.get_idx("x-y", sl=sl, idx=idx, pos=pos)
        paths = {
            self.pos_to_idx(z, "z"): path for z, path in self._z_paths.items()
        }
        return skrt.core.fullpath(paths[idx])

    def get_dicom_dataset(self, sl=None, idx=None, pos=None):
        """Return pydicom.dataset.FileDataset object associated with this Image
        if it was loaded from dicom; otherwise, return None.

        If any of <sl>, <idx> or <pos> are provided, the dataset corresponding
        to that specific slice will be returned; otherwise, the last loaded
        dataset will be returned.

        Parameters
        ----------
        sl : int, default=None
            Slice number; used if not None.

        idx : int, default=None
            Slice array index; used if not None and <sl> is None.

        pos : float, default=None
            Slice position in mm; used if not None and <sl> and <idx> are both
            None.

        """

        self.load()

        # If no specific slice is requested, just return the last loaded dataset
        if sl is None and idx is None and pos is None:
            return self.dicom_dataset

        # Otherwise, load the dataset for that slice
        return pydicom.dcmread(
            self.get_dicom_filepath(sl=sl, idx=idx, pos=pos), force=True
        )

    def get_voxel_size(self):

        """Return voxel sizes in mm in order [x, y, z]."""

        self.load()
        return self.voxel_size

    def get_origin(self):
        """Return origin position in mm in order [x, y, z]."""

        self.load()
        return self.origin

    def get_n_voxels(self):
        """Return number of voxels in order [x, y, z]."""

        self.load()
        return self.n_voxels

    def get_affine(self, standardise=False):
        """Return affine matrix.

        Parameters
        ----------
        standardise : bool, default=False
            If False, the affine matrix will be returned in the orientation in 
            which it was loaded; otherwise, it will be returned in standard
            dicom-style orientation such that [column, row, slice] corresponds
            to the [x, y, z] axes.
        """

        self.load()
        if not standardise:
            return self.affine
        else:
            return self.get_standardised_affine(force=True)

    def get_structure_sets(self):
        """Return list of StructureSet objects associated with this Image."""

        return self.structure_sets

    def load(self, force=False):
        """Load pixel array from image source. If already loaded and <force> 
        is False, nothing will happen.

        Parameters
        ----------
        force : bool, default=True
            If True, the pixel array will be reloaded from source even if it 
            has previously been loaded.

        Data loading takes input from self.source and uses this to assign
        self.data (as well as geometric properties, where relevant). The 
        parameter self.source_type is set to a string indicating the type 
        of source, which can be any of:
            - "array": 
                Data loaded from a numpy array in dicom-style orientation.
            - "nifti array": 
                Data loaded from a numpy array in nifti-style orientation.
            - "nifti": 
                Data loaded from a nifti file.
            - "dicom": 
                Data loaded from one or more dicom file(s).

        The loading sequence is as follows: 

            1. If self.source is a numpy array, self.data will be set to the
            contents of self.source. If <nifti_array> was set to True when 
            __init__() was called, self.source_type is set to "nifti array";
            otherwise, self.source_type is set to "array".

            2. If self.source is a string, this is treated as a filepath. 
            Attempt to load a nifti file from this path using the function
            load_nifti(). If the path points to a valid nifti file, this will
            return a pixel array and affine matrix, which are assigned to
            self.data and self.affine, respectively. Set self.source_type
            to "nifti".
           
            3. If self.source is neither a numpy array nor a string, throw a
            TypeError.

            4. If no data were loaded in step 2 (i.e. self.data is still None),
            attempt to load from a numpy binary file at the path in self.source 
            using the function load_npy(). If the path points to a valid numpy
            binary file, this will return a pixel array, which is assigned to
            self.data. Set source_type to either "nifti array" or "array",
            depending on whether <nifti_array> was set to True or False, 
            respectively, when __init__() was called.

            5. If no data were loaded in step 4 (i.e. self.data is still None),
            attempt to load from a dicom file or directory at the path in
            self.source using the function load_dicom(). If successful, this 
            returns a pixel array, affine matrix, default greyscale window
            centre and width, the last loaded pydicom.dataset.FileDataset 
            object, and a dictionary mapping z positions to paths to the
            dicom file for that slice. These outputs are used to assign 
            self.data, self.affine, self.dicom_dataset, and self._z_paths; 
            self.source_type is set to "dicom".

            6. If no data were loaded in step 5 (i.e. self.data is still None),
            raise a RuntimeError.

            7. If self.data contains a 2D array, convert this to 3D by adding
            an extra axis.

            8. Apply any downsampling as specificied in __init__().

            9. Run self.set_geometry() in order to compute geometric quantities
            for this Image.

            10. If a default window width and window centre were loaded from 
            dicom, use these to set self.default_window to a greyscale window 
            range.

            11. If self.title is None and self.source is a filepath, infer
            a title from the basename of this path.
        """

        if self.data is not None and not force:
            return

        window_width = None
        window_centre = None

        # Load image array from source
        # Numpy array
        if isinstance(self.source, np.ndarray):
            self.data = self.source
            self.source_type = "nifti array" if self.nifti_array else "array"

        # Try loading from nifti file
        elif isinstance(self.source, str):
            if not os.path.exists(self.source):
                raise RuntimeError(
                    f"Image input {self.source} does not exist!")
            if os.path.isfile(self.source):
                self.data, affine = load_nifti(self.source)
                self.source_type = "nifti"
            if self.data is not None:
                self.affine = affine

        else:
            raise TypeError("Unrecognised image source type:", self.source)

        # Try loading from numpy file
        if self.data is None:
            self.data = load_npy(self.source)
            self.source_type = "nifti array" if self.nifti_array else "array"

        # Try loading from dicom file
        if self.data is None:
            self.data, affine, window_centre, window_width, ds, self._z_paths \
                    = load_dicom(self.source)
            self.source_type = "dicom"
            if self.data is not None:
                self.dicom_dataset = ds
                self.affine = affine

        # If still None, raise exception
        if self.data is None:
            raise RuntimeError(f"{self.source} not a valid image source!")

        # Ensure array is 3D
        if self.data.ndim == 2:
            self.data = self.data[..., np.newaxis]

        # Apply downsampling
        if self.downsampling:
            self.downsample(self.downsampling)
        else:
            self.set_geometry()

        # Set default grayscale range
        if window_width and window_centre:
            self.default_window = [
                window_centre - window_width / 2,
                window_centre + window_width / 2,
            ]
        else:
            self.default_window = [-300, 200]

        # Set title from filename
        if self.title is None:
            if isinstance(self.source, str) and os.path.exists(self.source):
                self.title = os.path.basename(self.source)

    def get_standardised_data(self, force=True):
        """Return array in standard dicom orientation, where 
        [column, row, slice] corresponds to the [x, y, z] axes.
        standardised image array. 

        Parameters
        ----------
        force : bool, default=True
            If True, the standardised array will be recomputed from self.data 
            even if it has previously been computed.
        """

        if not hasattr(self, "_sdata") or force:
            self.standardise_data()
        return self._sdata

    def get_standardised_affine(self, force=True):
        """Return affine matrix in standard dicom orientation, where 
        [column, row, slice] corresponds to the [x, y, z] axes.
        standardised image array. 

        Parameters
        ----------
        force : bool, default=True
            If True, the standardised array will be recomputed from self.data 
            even if it has previously been computed.
        """

        if not hasattr(self, "_saffine") or force:
            self.standardise_data()
        return self._saffine

    def standardise_data(self):
        """Manipulate data array and affine matrix into standard dicom
        orientation, where [column, row, slice] corresponds to the [x, y, z] 
        axes; assign results to self._sdata and self._saffine, respectively.
        Standardised voxel sizes (self._svoxel_size) and origin position
        (self._sorigin) will also be inferred from self._affine.
        """

        data = self.get_data()
        affine = self.get_affine()

        # Adjust dicom
        if self.source_type == "dicom":

            # Transform array to be in order (row, col, slice) = (x, y, z)
            orient = np.array(self.get_orientation_vector()).reshape(2, 3)
            axes = self.get_axes()
            axes_colrow = self.get_axes(col_first=True)
            transpose = [axes_colrow.index(i) for i in (1, 0, 2)]
            data = np.transpose(self.data, transpose).copy()

            # Adjust affine matrix
            affine = self.affine.copy()
            for i in range(3):

                # Voxel sizes
                if i != axes.index(i):
                    voxel_size = affine[i, axes.index(i)].copy()
                    affine[i, i] = voxel_size
                    affine[i, axes.index(i)] = 0

                # Invert axis direction if negative
                if axes.index(i) < 2 and orient[axes.index(i), i] < 0:
                    affine[i, i] *= -1
                    to_flip = [1, 0, 2][i]
                    data = np.flip(data, axis=to_flip)
                    n_voxels = data.shape[to_flip]
                    affine[i, 3] = affine[i, 3] - (n_voxels - 1) * affine[i, i]

        # Adjust nifti
        elif "nifti" in self.source_type:

            init_dtype = self.get_data().dtype
            nii = nibabel.as_closest_canonical(
                nibabel.Nifti1Image(self.data.astype(np.float64), self.affine)
            )
            data = nii.get_fdata().transpose(1, 0, 2)[::-1, ::-1, :].astype(
                init_dtype)
            affine = nii.affine

            # Reverse x and y directions
            affine[0, 3] = -(affine[0, 3] + (data.shape[1] - 1) * affine[0, 0])
            affine[1, 3] = -(affine[1, 3] + (data.shape[0] - 1) * affine[1, 1])

        # Assign standardised image array and affine matrix
        self._sdata = data
        self._saffine = affine

        # Get standard voxel sizes and origin
        self._svoxel_size = list(np.diag(self._saffine))[:-1]
        self._sorigin = list(self._saffine[:-1, -1])

    def resample(self, voxel_size=(1, 1, 1), order=1):
        '''
        Resample image to have particular voxel sizes.

        Parameters
        ----------
        voxel_size: int/float/tuple/list, default =(1, 1, 1)
            Voxel size to which image is to be resampled.  If voxel_size is
            a tuple or list, then it's taken to specify voxel sizes
            in mm in the order x, y, z.  If voxel_size is an int or float,
            then it's taken to specify the voxel size in mm along all axes.

        order: int, default = 1
            Order of the b-spline used in interpolating voxel intensity values.
        '''

        self.load()
        if isinstance(voxel_size, float) or isinstance(voxel_size, int):
            voxel_size = 3 * [voxel_size]

        # Fill in None values with own voxel size
        voxel_size2 = []
        for i, v in enumerate(voxel_size):
            if v is not None:
                voxel_size2.append(v)
            else:
                voxel_size2.append(self.voxel_size[i])
        voxel_size = voxel_size2

        # Define scale factors to obtain requested voxel size
        scale = [self.voxel_size[i] / voxel_size[i] for i in range(3)]

        self.data = scipy.ndimage.zoom(self.data, scale, order=order,
                mode='nearest')


        # Reset properties
        self.origin = [
            self.origin[i] - self.voxel_size[i] / 2 + voxel_size[i] / 2
            for i in range(3)
        ]

        ny, nx, nz = self.data.shape
        self.n_voxels = [ny, nx, nz]
        self.voxel_size = voxel_size
        self.affine = None
        self.set_geometry()

    def get_coordinate_arrays(self, image_size, origin, voxel_size):
        '''
        Obtain (x, y, z) arrays of coordinates of voxel centres.

        Arrays are useful for image resizing.

        Parameters
        ----------
        image_size : tuple
            Image size in voxels, in order (x,y,z).

        origin : tuple
            Origin position in mm in order (x, y, z).

        voxel_size : tuple
            Voxel sizes in mm in order (x, y, z).
        '''

        self.load()

        # Extract parameters for determining coordinates of voxel centres.
        nx, ny, nz = image_size
        x, y, z = origin
        dx, dy, dz = voxel_size
  
        # Obtain coordinate arrays.
        try:
            x_array = np.linspace(x, x + (nx - 1) * dx, nx)
        except TypeError:
            x_array = None
        try:
            y_array = np.linspace(y, y + (ny - 1) * dy, ny)
        except TypeError:
            y_array = None
        try:
            z_array = np.linspace(z, z + (nz - 1) * dz, nz)
        except TypeError:
            z_array = None

        return (x_array, y_array, z_array)

    def resize(self, image_size=None, image_size_unit=None, origin=None,
            voxel_size=None, fill_value=None):
        '''
        Resize image to specified image size, voxel size and origin.

        Parameters
        ----------
        image_size : tuple/None, default=None
            Image sizes in order (x,y,z) to which image is to be resized,  If None,
            the image's existing size is kept.  The unit of measurement
            ('voxel' or 'mm') is specified via image_size_unit.  If the size
            is in mm, and isn't an integer multiple of voxel_size, resizing
            won't be exact.

        image_size_unit : str, default=None
            Unit of measurement ('voxel' or 'mm') for image_size.  If None,
            use 'voxel'.

        origin : tuple, default=(0, 0, 0)
            Origin position in mm in order (x, y, z).  If None, the image's
            existing origin is kept.

        voxel_size : tuple/None, default=None
            Voxel sizes in mm in order (x, y, z).  If None, the image's
            existing origin is kept.

        fill_value: float/None, default = None
            Intensity value to be assigned to any voxels in the resized
            image that are outside the original image.  If set to None,
            the minimum intensity value of the original image is used.
        '''
        # Return if no resizing requested.
        if image_size is None and voxel_size is None and origin is None:
            return

        # Ensure that data are loaded.
        self.load()

        # Ensure that resizing values are defined.
        allowed_unit = ['mm', 'voxel']
        if image_size_unit is None or image_size_unit not in allowed_unit:
            image_size_unit = 'voxel'
        if image_size is None:
            image_size = self.get_n_voxels()

        if voxel_size is None:
            voxel_size = self.get_voxel_size()

        if origin is None:
            origin = self.get_origin()

        if 'mm' == image_size_unit:
            image_size = [math.ceil(image_size[i] / voxel_size[i]) for i in range(3)]

        # Allow for two-dimensional images
        if 2 == len(self.get_data().shape):
            ny, nx = self.get_data().shape
            self.data = self.get_data().reshape(ny, nx, 1)

        nx, ny, nz = image_size

        # Check whether image is already the requested size
        match = (self.get_data().shape == [ny, nx, nz]
            and (self.get_origin() == origin) \
            and (self.get_voxel_size() == voxel_size))

        if not match:

            # If slice thickness not known, set to the requested value
            if self.get_voxel_size()[2] is None:
                self.voxel_size[2] = image.voxel_size[2]


            # Set fill value
            if fill_value is None:
                fill_value = self.get_data().min()

            #print(f"interpolation start time: {time.strftime('%c')}")
            x1_array, y1_array, z1_array = self.get_coordinate_arrays(
                    self.get_n_voxels(), self.get_origin(), self.get_voxel_size())
            if not (x1_array is None or y1_array is None or z1_array is None):
                # Define how intensity values are to be interpolated
                # for the original image
                interpolant = scipy.interpolate.RegularGridInterpolator(
                        (y1_array, x1_array, z1_array),
                        self.get_data(),
                        method="linear",
                        bounds_error=False,
                        fill_value=fill_value)

                # Define grid of voxel centres for the resized image
                x2_array, y2_array, z2_array = self.get_coordinate_arrays(
                        image_size, origin, voxel_size)
                nx, ny, nz = image_size
                meshgrid = np.meshgrid(
                        y2_array, x2_array, z2_array, indexing="ij")
                vstack = np.vstack(meshgrid)
                point_array = vstack.reshape(3, -1).T.reshape(ny, nx, nz, 3)

                # Perform resizing
                self.data = interpolant(point_array)

                # Reset geometry
                self.voxel_size = voxel_size
                self.origin= origin
                self.n_voxels = image_size
                self.affine = None
                self.set_geometry()

            #print(f"interpolation end time: {time.strftime('%c')}")

        return None

    def match_size(self, image=None, fill_value=None):

        '''
        Match image size to that of a reference image.

        After matching, the image voxels are in one-to-one correspondence
        with those of the reference.

        Parameters
        ----------
        image: skrt.image.Image/None, default=None
            Reference image, with which size is to be matched.
        fill_value: float/None, default = None
            Intensity value to be assigned to any voxels in the resized
            image that are outside the original image.  If set to None,
            the minimum intensity value of the original image is used.
        '''

        self.resize(image.get_n_voxels(), None, image.get_origin(),
                image.get_voxel_size(), fill_value)

        return None

    def match_voxel_size(self, image, method="self"):
        """Resample to match z-axis voxel size with that of another Image
        object.

        Parameters
        ----------
        image : Image
            Other image to which z-axis voxel size should be matched.

        method : str, default="self"
            String specifying the matching method. Can be any of:
                - "self": 
                    Match own z voxel size to that of <image>.
                - "coarse": 
                    Resample the image with the smaller z voxels to match
                    that of the image with larger z voxels.
                - "fine": 
                    Resample the image with the larger z voxels to match
                    that of the image with smaller z voxels.
        """

        # Determine which image should be resampled
        if method == "self":
            to_resample = self
            match_to = image
        else:
            own_vz = self.get_voxel_size()[2]
            other_vz = image.get_voxel_size()[2]
            if own_vz == other_vz:
                print("Voxel sizes already match! No resampling applied.")
                return
            if method == "coarse":
                to_resample = self if own_vz < other_vz else image
                match_to = self if own_vz > other_vz else image
            elif method == "fine":
                to_resample = self if own_vz > other_vz else image
                match_to = self if own_vz < other_vz else image
            else:
                raise RuntimeError(f"Unrecognised resampling option: {method}")

        # Perform resampling
        voxel_size = [None, None, match_to.get_voxel_size()[2]]
        init_vz = to_resample.voxel_size[2]
        init_nz = int(to_resample.n_voxels[2])
        to_resample.resample(voxel_size)
        print(
            f"Resampled z axis from {init_nz} x {init_vz:.3f} mm -> "
            f"{int(to_resample.n_voxels[2])} x {to_resample.voxel_size[2]:.3f}"
            "mm"
        )

    def get_min(self):
        """Get minimum greyscale value of data array."""

        self.load()
        return self.data.min()

    def get_max(self):
        """Get maximum greyscale value of data array."""

        self.load()
        return self.data.max()

    def get_orientation_codes(self, affine=None, source_type=None):
        """Get image orientation codes in order [row, column, slice] if 
        image was loaded in dicom-style orientation, or [column, row, slice] 
        if image was loaded in nifti-style orientation.

        This returns a list of code strings. Possible codes:
            "L" = Left (x axis)
            "R" = Right (x axis)
            "P" = Posterior (y axis)
            "A" = Anterior (y axis)
            "I" = Inferior (z axis)
            "S" = Superior (z axis)

        Parameters
        ----------
        affine : np.ndarray, default=None
            Custom affine matrix to use when determining orientation codes.
            If None, self.affine will be used.

        source_type : str, default=None
            Assumed source type to use when determining orientation codes. If 
            None, self.source_type will be used.
        """

        if affine is None:
            self.load()
            affine = self.affine
        codes = list(nibabel.aff2axcodes(affine))

        if source_type is None:
            source_type = self.source_type

        # Reverse codes for row and column of a dicom
        pairs = [("L", "R"), ("P", "A"), ("I", "S")]
        if "nifti" not in source_type:
            for i in range(2):
                switched = False
                for p in pairs:
                    for j in range(2):
                        if codes[i] == p[j] and not switched:
                            codes[i] = p[1 - j]
                            switched = True

        return codes

    def get_orientation_vector(self, affine=None, source_type=None):
        """Get image orientation as a row and column vector.

        Parameters
        ----------
        affine : np.ndarray, default=None
            Custom affine matrix to use when determining orientation vector.
            If None, self.affine will be used.

        source_type : str, default=None
            Assumed source type to use when determining orientation vector. If 
            None, self.source_type will be used.
        """

        if source_type is None:
            source_type = self.source_type
        if affine is None:
            affine = self.affine

        codes = self.get_orientation_codes(affine, source_type)
        if "nifti" in source_type:
            codes = [codes[1], codes[0], codes[2]]
        vecs = {
            "L": [1, 0, 0],
            "R": [-1, 0, 0],
            "P": [0, 1, 0],
            "A": [0, -1, 0],
            "S": [0, 0, 1],
            "I": [0, 0, -1],
        }
        return vecs[codes[0]] + vecs[codes[1]]

    def get_axes(self, col_first=False):
        """Return list of axis numbers in order [column, row, slice] if
        col_first is True, otherwise in order [row, column, slice]. The axis
        numbers 0, 1, and 2 correspond to x, y, and z, respectively.

        Parameters
        ----------
        col_first : bool, default=True
            If True, return axis numbers in order [column, row, slice] instead
            of [row, column, slice].
        """

        orient = np.array(self.get_orientation_vector()).reshape(2, 3)
        axes = [sum([abs(int(orient[i, j] * j)) for j in range(3)]) for i in
                range(2)]
        axes.append(3 - sum(axes))
        if not col_first:
            return axes
        else:
            return [axes[1], axes[0], axes[2]]

    def get_machine(self, stations=_default_stations):

        machine = None
        if self.files:
            ds = pydicom.dcmread(self.files[0].path, force=True)
            try:
                station = ds.StationName
            except BaseException:
                station = None
            if station in stations:
                machine = stations[station]
        return machine

    def set_geometry(self):
        """Set geometric properties for this image. Should be called once image
        data has been loaded. Sets the following properties:

            Affine matrix (self.affine): 
                4x4 affine matrix. If initially None, this is computed 
                from self.voxel_size and self.origin.

            Voxel sizes (self.voxel_size):
                List of [x, y, z] voxel sizes in mm. If initially None, this 
                is computed from self.affine.

            Origin position (self.origin):
                List of [x, y, z] origin coordinates in mm. If initiall None,
                this is computed from self.affine.

            Number of voxels (self.n_voxels):
                List of number of voxels in the [x, y, z] directions. Computed 
                from self.data.shape.

            Limits (self.lims):
                List of minimum and maximum array positions in the [x, y, z]
                directions (corresponding the centres of the first and last 
                voxels). Calculated from standardised origin, voxel sizes, 
                and image shape.

            Image extent (self.image_extent):
                List of minimum and maximum positions of the edges of the array
                in the [x, y, z] directions. Calculated from self.lims +/-
                half a voxel size.

            Plot extent (self.plot_extent):
                Dict of plot extents for each orientation. Given in the form
                of a list [x1, x2, y1, y2], which is the format needed for 
                the <extent> argument for matplotlib plotting.
        """

        # Set affine matrix, voxel sizes, and origin
        self.affine, self.voxel_size, self.origin = \
                get_geometry(
                    self.affine, self.voxel_size, self.origin, 
                    is_nifti=("nifti" in self.source_type),
                    shape=self.data.shape
                )

        # Set number of voxels
        self.n_voxels = [self.data.shape[1], self.data.shape[0],
                         self.data.shape[2]]

        # Set axis limits for standardised plotting
        self.standardise_data()
        self.lims = [
            (
                self._sorigin[i],
                self._sorigin[i] + (self.n_voxels[i] - 1) * self._svoxel_size[i],
            )
            for i in range(3)
        ]
        self.image_extent = [
            (
                self.lims[i][0] - self._svoxel_size[i] / 2,
                self.lims[i][1] + self._svoxel_size[i] / 2,
            )
            for i in range(3)
        ]
        self.plot_extent = {
            view: self.image_extent[x_ax] + self.image_extent[y_ax][::-1]
            for view, (x_ax, y_ax) in _plot_axes.items()
        }

    def get_length(self, ax):
        """Get image length along a given axis.

        Parameters
        ----------
        ax : str/int
            Axis along which to get length. Can be either "x", "y", "z" or
            0, 1, 2.
        """

        self.load()
        if not isinstance(ax, int):
            ax = _axes.index(ax)
        return abs(self.lims[ax][1] - self.lims[ax][0])

    def get_idx(self, view, sl=None, idx=None, pos=None):
        """Get an array index from either a slice number, index, or
        position. If <sl>, <idx>, and <pos> are all None, the index of the 
        central slice of the image in the orienation specified in <view> will 
        be returned.

        Parameters
        ----------
        view : str
            Orientation in which to compute the index. Can be "x-y", "y-z", or
            "x-z".

        sl : int, default=None
            Slice number. If given, this number will be converted to an index 
            and returned.

        idx : int, default=None
            Slice array index. If given and <sl> is None, this index will be 
            returned.

        pos : float, default=None
            Slice position in mm. If given and <sl> and <idx> are both None,
            this position will be converted to an index and returned.
        """

        if sl is not None:
            idx = self.slice_to_idx(sl, _slice_axes[view])
        elif idx is None:
            if pos is not None:
                idx = self.pos_to_idx(pos, _slice_axes[view])
            else:
                centre_pos = self.get_centre()[_slice_axes[view]]
                idx = self.pos_to_idx(centre_pos, _slice_axes[view])
        return idx

    def get_slice(
        self, view="x-y", sl=None, idx=None, pos=None, flatten=False, 
        force=True, **kwargs
    ):
        """Get a slice of the data in the correct orientation for plotting. 
        If <sl>, <pos>, and <idx> are all None, the central slice of the image
        in the orientation specified in <view> will be returned.

        Parameters
        ----------
        view : str
            Orientation; can be "x-y", "y-z", or "x-z".

        sl : int, default=None
            Slice number; used if not None.

        idx : int, default=None
            Slice array index; used if not None and <sl> is None.

        pos : float, default=None
            Slice position in mm; used if not None and <sl> and <idx> are both
            None.

        flatten : bool, default=False
            If True, the image will be summed across all slices in the 
            orientation specified in <view>; <sl>/<idx>/<pos> will be ignored.
        """

        # Get index
        idx = self.get_idx(view, sl=sl, idx=idx, pos=pos)

        # Check whether index and view match cached slice
        if hasattr(self, "_current_slice") and not force and not flatten:
            if self._current_idx == idx and self._current_view == view:
                return self._current_slice

        # Create slice
        transposes = {"x-y": (0, 1, 2), "y-z": (0, 2, 1), "x-z": (1, 2, 0)}
        transpose = transposes[view]
        list(_plot_axes[view]) + [_slice_axes[view]]
        data = np.transpose(self.get_standardised_data(force=True), transpose)
        if flatten:
            return np.sum(data, axis=2)
        else:
            # Cache the slice
            self._current_slice = data[:, :, idx]
            self._current_idx = idx
            self._current_view = view
            return self._current_slice

    def add_structure_set(self, structure_set):
        """Add a structure set to be associated with this image. This Image 
        object will simultaneously be assigned to the StructureSet.

        Parameters
        ----------
        structure_set : skrt.structures.StructureSet
            A StructureSet object to assign to this image.
        """

        self.structure_sets.append(structure_set)
        structure_set.set_image(self)

    def clear_structure_sets(self):
        """Clear all structure sets associated with this image."""

        self.structure_sets = []

    def get_mpl_kwargs(self, view, mpl_kwargs=None, scale_in_mm=True):
        """Get a dict of kwargs for plotting this image in matplotlib. This
        will create a default dict, which is updated to contain any kwargs
        contained in <mpl_kwargs>.

        The default parameters are:
            - "aspect":
                Aspect ratio determined from image geometry.
            - "extent": 
                Plot extent determined from image geometry.
            - "cmap":
                Colormap, "gray" by default.
            - "vmin"/"vmax"
                Greyscale range to use; taken from self.default_window by 
                default.

        Parameters
        ----------
        view : str
            Orientation (any of "x-y", "x-z", "y-z"); needed to compute
            correct aspect ratio and plot extent.

        mpl_kwargs : dict, default=None
            Dict of kwargs with which to overwrite default kwargs.

        scale_in_mm : bool, default=True
            If True, indicates that image will be plotted with axis scales in 
            mm; needed to compute correct aspect ratio and plot extent.
        """

        if mpl_kwargs is None:
            mpl_kwargs = {}

        # Set colormap
        if "cmap" not in mpl_kwargs:
            mpl_kwargs["cmap"] = "gray"

        # Set colour range
        for i, name in enumerate(["vmin", "vmax"]):
            if name not in mpl_kwargs:
                mpl_kwargs[name] = self.default_window[i]

        # Set image extent and aspect ratio
        extent = self.plot_extent[view]
        mpl_kwargs["aspect"] = 1
        x_ax, y_ax = _plot_axes[view]
        if not scale_in_mm:
            extent = [
                self.pos_to_slice(extent[0], x_ax, False),
                self.pos_to_slice(extent[1], x_ax, False),
                self.pos_to_slice(extent[2], y_ax, False),
                self.pos_to_slice(extent[3], y_ax, False),
            ]
            mpl_kwargs["aspect"] = abs(self.voxel_size[y_ax]
                                       / self.voxel_size[x_ax])
        mpl_kwargs["extent"] = extent

        return mpl_kwargs

    def view(self, **kwargs):
        """View self with BetterViewer. Any **kwargs will be passed to 
        BetterViewer initialisation.
        """

        from skrt.better_viewer import BetterViewer

        return BetterViewer(self, **kwargs)

    def plot(
        self,
        view="x-y",
        sl=None,
        idx=None,
        pos=None,
        scale_in_mm=True,
        ax=None,
        gs=None,
        figsize=_default_figsize,
        save_as=None,
        zoom=None,
        zoom_centre=None,
        hu=None,
        mpl_kwargs=None,
        show=True,
        colorbar=False,
        colorbar_label="HU",
        title=None,
        no_ylabel=False,
        annotate_slice=False,
        major_ticks=None,
        minor_ticks=None,
        ticks_all_sides=False,
        no_axis_labels=False,
        structure_set=None,
        rois=None,
        roi_plot_type="contour",
        legend=False,
        roi_kwargs={},
        centre_on_roi=None,
        legend_loc="lower left",
        flatten=False,
        xlim=None,
        ylim=None,
        zlim=None
    ):
        """Plot a 2D slice of the image.

        Parameters
        ----------
        view : str, default='x-y'
            Orientation in which to plot the image. Can be any of 'x-y',
            'y-z', and 'x-z'.

        sl : int, default=None
            Slice number to plot. Takes precedence over <idx> and <pos> if not
            None. If all of <sl>, <idx>, and <pos> are None, the central
            slice will be plotted.

        idx : int, default=None
            Index of the slice in the array to plot. Takes precendence over
            <pos>.

        pos : float, default=None
            Position in mm of the slice to plot. Will be rounded to the nearest
            slice. Only used if <sl> and <idx> are both None.

        standardised : bool, default=True
            If True, a standardised version of the image array will be plotted
            such that the axis labels are correct.

        scale_in_mm : bool, default=True
            If True, axis labels will be in mm; otherwise, they will be slice
            numbers.

        ax : matplotlib.pyplot.Axes, default=None
            Axes on which to plot. If None, new axes will be created.

        gs : matplotlib.gridspec.GridSpec, default=None
            If not None and <ax> is None, new axes will be created on the
            current matplotlib figure with this gridspec.

        figsize : float, default=None
            Figure height in inches; only used if <ax> and <gs> are None.

        zoom : int/float/tuple, default=None
            Factor by which to zoom in. If a single int or float is given,
            the same zoom factor will be applied in all directions. If a tuple
            of three values is given, these will be used as the zoom factors
            in each direction in the order (x, y, z). If None, the image will
            not be zoomed in.

        zoom_centre : tuple, default=None
            Position around which zooming is applied. If None, the centre of
            the image will be used.

        colorbar : bool, default=True
            If True, a colorbar will be drawn alongside the plot.

        colorbar_label : str, default='HU'
            Label for the colorbar, if drawn.

        hu : list, default=None
            Two-item list containing min and max HU for plotting. Supercedes
            'vmin' and 'vmax' in <mpl_kwargs>.

        mpl_kwargs : dict, default=None
            Dictionary of keyword arguments to pass to matplotlib.imshow().

        show : bool, default=True
            If True, the plotted figure will be shown via
            matplotlib.pyplot.show().

        title : str, default=None
            Custom title for the plot. If None, a title inferred from the image
            filename will be used. If False or '', no title will be added.

        no_ylabel : bool, default=False
            If True, the y axis will not be labelled.

        annotate_slice : bool/str, default=False
            Color for annotation of slice number. If False, no annotation will
            be added. If True, the default color (white) will be used.

        major_ticks : float, default=None
            If not None, this value will be used as the interval between major
            tick marks. Otherwise, automatic matplotlib axis tick spacing will
            be used.

        minor_ticks : int, default=None
            If None, no minor ticks will be plotted. Otherwise, this value will
            be the number of minor tick divisions per major tick interval.

        ticks_all_sides : bool, default=False
            If True, major (and minor if using) tick marks will be shown above
            and to the right hand side of the plot as well as below and to the
            left. The top/right ticks will not be labelled.

        rois : int/str, default=None
            Option for which structure set should be plotted (if the Image
            owns any structure sets). Can be:
                - None: no structure sets will be plotted.
                - The index in self.structure_sets of the structure set (e.g. to plot
                the newest structure set, use structure_set=-1)
                - 'all': all structure sets will be plotted.

        roi_plot_type : str, default='contour'
            ROI plotting type (see ROI.plot() for options).

        legend : bool, default=False
            If True, a legend will be drawn containing ROI names.

        roi_kwargs : dict, default=None
            Extra arguments to provide to ROI plotting via the ROI.plot() method.

        centre_on_roi : str, default=None
            Name of ROI on which to centre, if no idx/sl/pos is given.
            If <zoom> is given but no <zoom_centre>, the zoom will also centre
            on this ROI.

        legend_loc : str, default='lower left'
            Legend location for ROI legend.

        xlim, ylim : tuples, default=None
            Custom limits on the x and y axes of the plot.
        """

        self.load()

        # Set up axes
        self.set_ax(view, ax, gs, figsize, zoom, colorbar)
        self.ax.clear()
        self.load()

        # Get list of input ROI sources
        if rois is None:
            roi_input = []
        elif isinstance(rois, str) and rois == "all":
            roi_input = self.structure_sets
        elif not skrt.core.is_list(rois):
            roi_input = [rois]
        else:
            roi_input = rois

        # Get list of ROI objects to plot
        rois_to_plot = []
        n_structure_sets = 0
        for roi in roi_input:
            if type(roi).__name__ == "ROI":
                rois_to_plot.append(roi)
            elif type(roi).__name__ == "StructureSet":
                rois_to_plot.extend(roi.get_rois())
                n_structure_sets += 1
            elif isinstance(roi, int):
                try:
                    rois_to_plot.extend(self.structure_sets[roi].get_rois())
                    n_structure_sets += 1
                except IndexError:
                    raise IndexError(f"Index {roi} not found in Image.structure_sets!")

        # If centering on an ROI, find index of its central slice
        roi_names = [roi.name for roi in rois_to_plot]
        if centre_on_roi:
            central_roi = rois_to_plot[roi_names.index(centre_on_roi)]
            idx = central_roi.get_mid_idx(view)
            if zoom and zoom_centre is None:
                zoom_centre = central_roi.get_zoom_centre(view)

        # Get image slice
        idx = self.get_idx(view, sl=sl, idx=idx, pos=pos)
        image_slice = self.get_slice(view, idx=idx, flatten=flatten)

        # Apply HU window if given
        if mpl_kwargs is None:
            mpl_kwargs = {}
        if hu is not None:
            mpl_kwargs["vmin"] = hu[0]
            mpl_kwargs["vmax"] = hu[1]

        # Plot the slice
        mesh = self.ax.imshow(
            image_slice, **self.get_mpl_kwargs(view, mpl_kwargs, scale_in_mm)
        )

        # Plot ROIs
        roi_handles = []
        for roi in rois_to_plot:

            # Plot the ROI on same axes
            if roi.on_slice(view, idx=idx):
                roi.plot(
                    view,
                    idx=idx,
                    ax=self.ax,
                    plot_type=roi_plot_type,
                    show=False,
                    include_image=False,
                    **roi_kwargs
                )

                # Add patch to list of handles for legend creation
                if legend:

                    # Get ROI name and append structure set name if multiple
                    # structure sets are being plotted
                    name = roi.name
                    if n_structure_sets > 1 and hasattr(roi, "structure_set"):
                        name += f" ({roi.structure_set.name})"
                    roi_handles.append(mpatches.Patch(color=roi.color,
                                                      label=name))

         # Draw ROI legend
        if legend and len(roi_handles):
            self.ax.legend(
                handles=roi_handles, loc=legend_loc, facecolor="white",
                framealpha=1
            )

        # Label axes
        self.label_ax(
            view,
            idx,
            scale_in_mm,
            title,
            no_ylabel,
            annotate_slice,
            major_ticks,
            minor_ticks,
            ticks_all_sides,
            no_axis_labels
        )
        self.zoom_ax(view, zoom, zoom_centre)

        # Set custom x and y limits
        if xlim is not None:
            self.ax.set_xlim(xlim)
        if ylim is not None:
            self.ax.set_ylim(ylim)

        # Add colorbar
        if colorbar and mpl_kwargs.get("alpha", 1) > 0:
            clb = self.fig.colorbar(mesh, ax=self.ax, label=colorbar_label)
            clb.solids.set_edgecolor("face")

        # Display image
        plt.tight_layout()
        if show:
            plt.show()

        # Save to file
        if save_as:
            self.fig.savefig(save_as)
            plt.close()

    def label_ax(
        self,
        view,
        idx,
        scale_in_mm=True,
        title=None,
        no_ylabel=False,
        annotate_slice=False,
        major_ticks=None,
        minor_ticks=None,
        ticks_all_sides=False,
        no_axis_labels=False,
        **kwargs,
    ):

        x_ax, y_ax = _plot_axes[view]

        # Set title
        if title is None:
            title = self.title
        if title:
            self.ax.set_title(title, pad=8)

        # Set axis labels
        units = " (mm)" if scale_in_mm else ""
        self.ax.set_xlabel(_axes[x_ax] + units, labelpad=0)
        if not no_ylabel:
            self.ax.set_ylabel(_axes[y_ax] + units)
        else:
            self.ax.set_yticks([])

        # Annotate with slice position
        if annotate_slice:
            z_ax = _axes[_slice_axes[view]]
            if scale_in_mm:
                z_str = "{} = {:.1f} mm".format(z_ax, self.idx_to_pos(idx,
                                                                      z_ax))
            else:
                z_str = "{} = {}".format(z_ax, self.idx_to_slice(idx, z_ax))
            if matplotlib.colors.is_color_like(annotate_slice):
                color = annotate_slice
            else:
                color = "white"
            self.ax.annotate(
                z_str,
                xy=(0.05, 0.93),
                xycoords="axes fraction",
                color=color,
                fontsize="large",
            )

        # Adjust tick marks
        if not no_axis_labels:
            if major_ticks:
                self.ax.xaxis.set_major_locator(MultipleLocator(major_ticks))
                self.ax.yaxis.set_major_locator(MultipleLocator(major_ticks))
            if minor_ticks:
                self.ax.xaxis.set_minor_locator(AutoMinorLocator(minor_ticks))
                self.ax.yaxis.set_minor_locator(AutoMinorLocator(minor_ticks))
            if ticks_all_sides:
                self.ax.tick_params(bottom=True, top=True, left=True, right=True)
                if minor_ticks:
                    self.ax.tick_params(
                        which="minor", bottom=True, top=True, left=True, right=True
                    )

        # Remove axis labels if needed
        if no_axis_labels:
            plt.axis("off")

    def zoom_ax(self, view, zoom=None, zoom_centre=None):
        """Zoom in on axes if needed."""

        if not zoom or isinstance(zoom, str):
            return
        zoom = skrt.core.to_list(zoom)
        x_ax, y_ax = _plot_axes[view]
        if zoom_centre is None:
            im_centre = self.get_centre()
            mid_x = im_centre[x_ax]
            mid_y = im_centre[y_ax]
        else:
            if len(zoom_centre) == 2:
                mid_x, mid_y = zoom_centre
            else:
                mid_x, mid_y = zoom_centre[x_ax], zoom_centre[y_ax]

        # Calculate new axis limits
        init_xlim = self.ax.get_xlim()
        init_ylim = self.ax.get_ylim()
        xlim = [
            mid_x - (init_xlim[1] - init_xlim[0]) / (2 * zoom[x_ax]),
            mid_x + (init_xlim[1] - init_xlim[0]) / (2 * zoom[x_ax]),
        ]
        ylim = [
            mid_y - (init_ylim[1] - init_ylim[0]) / (2 * zoom[y_ax]),
            mid_y + (init_ylim[1] - init_ylim[0]) / (2 * zoom[y_ax]),
        ]

        # Set axis limits
        self.ax.set_xlim(xlim)
        self.ax.set_ylim(ylim)

    def idx_to_pos(self, idx, ax, standardise=True):
        """Convert an array index to a position in mm along a given axis."""

        self.load()
        i_ax = _axes.index(ax) if ax in _axes else ax
        if standardise:
            origin = self._sorigin
            voxel_size = self._svoxel_size
        else:
            origin = self.origin
            voxel_size = self.voxel_size
        return origin[i_ax] + idx * voxel_size[i_ax]

    def pos_to_idx(self, pos, ax, return_int=True, standardise=True):
        """Convert a position in mm to an array index along a given axis."""

        self.load()
        i_ax = _axes.index(ax) if ax in _axes else ax
        if standardise:
            origin = self._sorigin
            voxel_size = self._svoxel_size
        else:
            origin = self.origin
            voxel_size = self.voxel_size
        idx = (pos - origin[i_ax]) / voxel_size[i_ax]
        if return_int:
            return round(idx)
        else:
            return idx

    def idx_to_slice(self, idx, ax):
        """Convert an array index to a slice number along a given axis."""

        self.load()
        i_ax = _axes.index(ax) if ax in _axes else ax
        if i_ax == 2:
            return self.n_voxels[i_ax] - idx
        else:
            return idx + 1

    def slice_to_idx(self, sl, ax):
        """Convert a slice number to an array index along a given axis."""

        self.load()
        i_ax = _axes.index(ax) if ax in _axes else ax
        if i_ax == 2:
            return self.n_voxels[i_ax] - sl
        else:
            return sl - 1

    def pos_to_slice(self, pos, ax, return_int=True, standardise=True):
        """Convert a position in mm to a slice number along a given axis."""

        sl = self.idx_to_slice(self.pos_to_idx(
            pos, ax, return_int, standardise=standardise), ax)
        if return_int:
            return round(sl)
        else:
            return sl

    def slice_to_pos(self, sl, ax, standardise=True):
        """Convert a slice number to a position in mm along a given axis."""

        return self.idx_to_pos(self.slice_to_idx(sl, ax), ax, standardise)

    def get_centre(self):
        """Get position in mm of the centre of the image."""

        self.load()
        return np.array([np.mean(self.lims[i]) for i in range(3)])

    def get_range(self, ax="z"):
        """Get range of the image in mm along a given axis."""

        i_ax = _axes.index(ax) if ax in _axes else ax
        origin = self.get_origin()[i_ax]
        return [origin, origin + (self.n_voxels[i_ax] - 1)
                * self.voxel_size[i_ax]]

    def get_length(self, ax="z"):
        """Get total length of image."""

        i_ax = _axes.index(ax) if ax in _axes else ax
        return abs(self.n_voxels[i_ax] * self.voxel_size[i_ax])

    def get_voxel_coords(self):
        """Get arrays of voxel coordinates in each direction."""

        return

    def set_ax(
        self,
        view=None,
        ax=None,
        gs=None,
        figsize=_default_figsize,
        zoom=None,
        colorbar=False
    ):
        """Set up axes for this Image."""

        skrt.image.set_ax(
            self, 
            view=view,
            ax=ax,
            gs=gs,
            figsize=figsize,
            zoom=zoom,
            colorbar=colorbar,
            aspect_getter=self.get_plot_aspect_ratio,
        )

    def get_plot_aspect_ratio(
        self, view, zoom=None, n_colorbars=0, figsize=_default_figsize
    ):
        """Estimate the ideal width/height ratio for a plot of this image
        in a given orientation.

        view : str
            Orientation ('x-y', 'y-z', or 'x-z')

        zoom : float/list, default=None
            Zoom factors; either a single value for all axes, or three values
            in order (x, y, z).

        n_colorbars : int, default=0
            Number of colorbars to make space for.
        """

        # Get length of the image in the plot axis directions
        self.load()
        x_ax, y_ax = _plot_axes[view]
        x_len = abs(self.lims[x_ax][1] - self.lims[x_ax][0])
        y_len = abs(self.lims[y_ax][1] - self.lims[y_ax][0])

        # Add padding for axis labels and title
        font = mpl.rcParams["font.size"] / 72
        y_pad = 2 * font
        if self.title:
            y_pad += 1.5 * font
        max_y_digits = np.floor(np.log10(max([abs(lim) for lim in
                                              self.lims[y_ax]])))
        minus_sign = any([lim < 0 for lim in self.lims[y_ax]])
        x_pad = (0.7 * max_y_digits + 1.2 * minus_sign + 1) * font

        # Account for zoom
        if zoom:
            zoom = skrt.core.to_list(zoom)
            x_len /= zoom[x_ax]
            y_len /= zoom[y_ax]

        # Add padding for colorbar(s)
        colorbar_frac = 0.4 * 5 / figsize
        x_len *= 1 + (n_colorbars * colorbar_frac)

        # Return estimated width ratio
        total_y = figsize + y_pad
        total_x = figsize * x_len / y_len + x_pad
        return total_x / total_y

    def downsample(self, downsampling):
        """Apply downsampling to the image array. Can be either a single
        value (to downsampling equally in all directions) or a list of 3
        values."""

        # Get downsampling in each direction
        if skrt.core.is_list(downsampling):
            if len(downsampling) != 3:
                raise TypeError("<downsample> must contain 3 elements!")
            dx, dy, dz = downsampling
        else:
            dx = dy = dz = downsampling

        # Apply to image array
        self.data = downsample(self.data, dx, dy, dz)

        # Adjust voxel sizes
        self.voxel_size = [v * d for v, d in zip(self.voxel_size,
                                                 [dx, dy, dz])]
        self.affine = None

        # Reset geometric properties of this image
        self.set_geometry()

    def get_nifti_array_and_affine(self, standardise=False):
        """Get image array and affine matrix in canonical nifti
        configuration."""

        # Convert dicom-style array to nifti
        if "nifti" not in self.source_type:
            data = self.get_data().transpose(1, 0, 2)[:, ::-1, :]
            affine = self.affine.copy()
            affine[0, :] = -affine[0, :]
            affine[1, 3] = -(affine[1, 3] + (data.shape[1] - 1)
                             * self.voxel_size[1])

        # Use existing nifti array
        else:
            data = self.get_data()
            affine = self.affine

        # Convert to canonical if requested
        if standardise:
            nii = nibabel.as_closest_canonical(nibabel.Nifti1Image(data,
                                                                   affine))
            return nii.get_fdata(), nii.affine
        else:
            return data, affine

    def get_dicom_array_and_affine(self, standardise=False):
        """Get image array and affine matrix in dicom configuration."""

        # Return standardised dicom array
        if standardise:
            self.standardise_data()
            return self._sdata, self._saffine

        # Convert nifti-style array to dicom
        if "nifti" in self.source_type:
            data = self.get_data().transpose(1, 0, 2)[::-1, :, :]
            affine = self.affine.copy()
            affine[0, :] = -affine[0, :]
            affine[1, 3] = -(affine[1, 3] + (data.shape[0] - 1)
                             * self.voxel_size[1])
            if standardise:
                nii = nibabel.as_closest_canonical(
                    nibabel.Nifti1Image(data, affine))
                return nii.get_fdata(), nii.affine
            else:
                return data, affine

        # Otherwise, return array as-is
        return self.get_data(), self.affine

    def write(
        self,
        outname,
        standardise=False,
        write_geometry=True,
        nifti_array=False,
        header_source=None,
        patient_id=None,
        modality=None,
        root_uid=None,
    ):
        """Write image data to a file. The filetype will automatically be
        set based on the extension of <outname>:

            (a) *.nii or *.nii.gz: Will write to a nifti file with
            canonical nifti array and affine matrix.

            (b) *.npy: Will write the dicom-style numpy array to a binary filem
            unless <nifti_array> is True, in which case the canonical
            nifti-style array will be written. If <write_geometry> is True,
            a text file containing the voxel sizes and origin position will
            also be written in the same directory.

            (c) *.dcm: Will write to dicom file(s) (1 file per x-y slice) in
            the directory of the filename given, named by slice number.

            (d) No extension: Will create a directory at <outname> and write
            to dicom file(s) in that directory (1 file per x-y slice), named
            by slice number.

        If (c) or (d) (i.e. writing to dicom), the header data will be set in
        one of three ways:
            - If the input source was not a dicom, <dicom_for_header> is None,
            a brand new dicom with freshly generated UIDs will be created.
            - If <dicom_for_header> is set to the path to a dicom file, that
            dicom file will be used as the header.
            - Otherwise, if the input source was a dicom or directory
            containing dicoms, the header information will be taken from the
            input dicom file.
        """

        outname = os.path.expanduser(outname)
        self.load()

        # Write to nifti file
        if outname.endswith(".nii") or outname.endswith(".nii.gz"):
            data, affine = self.get_nifti_array_and_affine(standardise)
            if data.dtype == "bool":
                data = data.copy().astype(int)
            write_nifti(outname, data, affine)
            print("Wrote to NIfTI file:", outname)

        # Write to numpy file
        elif outname.endswith(".npy"):
            if nifti_array:
                data, affine = self.get_nifti_array_and_affine(standardise)
            else:
                data, affine = self.get_dicom_array_and_affine(standardise)
            if not write_geometry:
                affine = None
            write_npy(outname, data, affine)
            print("Wrote to numpy file:", outname)

        # Write to dicom
        else:

            # Get name of dicom directory
            if outname.endswith(".dcm"):
                outdir = os.path.dirname(outname)
            else:
                outdir = outname

            # Get header source
            if header_source is None and self.source_type == "dicom":
                header_source = self.source
            data, affine = self.get_dicom_array_and_affine(standardise)
            orientation = self.get_orientation_vector(affine, "dicom")
            self.dicom_dataset = write_dicom(
                outdir,
                data,
                affine,
                header_source,
                orientation,
                patient_id,
                modality,
                root_uid,
            )
            print("Wrote dicom file(s) to directory:", outdir)

    def get_coords(self):
        """Get grids of x, y, and z coordinates for each voxel in the image."""

        # Make coordinates
        coords_1d = [
            np.arange(
                self.origin[i],
                self.origin[i] + self.n_voxels[i] * self.voxel_size[i],
                self.voxel_size[i],
            )
            for i in range(3)
        ]
        return np.meshgrid(*coords_1d)

    def transform(self, scale=1, translation=[0, 0, 0], rotation=[0, 0, 0],
            centre=[0, 0, 0], resample="fine", restore=True, order=1,
            fill_value=None):
        """Apply three-dimensional similarity transform using scikit-image.

        The image is first translated, then is scaled and rotated
        about the centre coordinates


        Parameters
        ----------
        scale : float, default=1
            Scaling factor.

        translation : list, default=[0, 0, 0]
            Translation in mm in the [x, y, z] directions.

        rotation : float, default=0
            Euler angles in degrees by which to rotate the image.
            Angles are in the order pitch (rotation about x-axis),
            yaw (rotation about y-axis), roll (rotation about z-axis).

        centre : list, default=[0, 0, 0]
            Coordinates in mm in [x, y, z] about which to perform rotation
            and scaling of translated image.

        resample: float/string, default='coarse'
            Resampling to be performed before image transformation.
            If resample is a float, then the image is resampled to that
            this is the voxel size in mm along all axes.  If the
            transformation involves scaling or rotation in an image
            projection where voxels are non-square, then:
            if resample is 'fine' then voxels are resampled to have
            their smallest size along all axes;
            if resample is 'coarse' then voxels are resampled to have
            their largest size along all axes.

        restore: bool, default=True
            In case that image has been resampled:
            if True, restore original voxel size for transformed iamge;
            if False, keep resampled voxel size for transformed image.

        order: int, default = 1
            Order of the b-spline used in interpolating voxel intensity values.

        fill_value: float/None, default = None
            Intensity value to be assigned to any voxels in the resized
            image that are outside the original image.  If set to None,
            the minimum intensity value of the original image is used.
        """

        self.load()

        # Decide whether to perform resampling.
        # The scipy.ndimage function affine_transform() assumes
        # that voxel sizes are the same # in all directions, so resampling
        # is necessary if the transformation # affects a projection in which
        # voxels are non-square.  In other cases resampling is optional.
        small_number = 0.1
        voxel_size = tuple(self.voxel_size)
        voxel_size_min = min(voxel_size)
        voxel_size_max = max(voxel_size)
        if isinstance(resample, int) or isinstance(resample, float):
            image_resample = True
        else:
            image_resample = False
        if not image_resample:
            if abs(voxel_size_max - voxel_size_min) > 0.1:
                if (scale - 1.) > small_number:
                    image_resample = True
                pitch, yaw, roll = rotation
                dx, dy, dz = voxel_size
                if abs(pitch) > small_number and abs(dy - dz) > small_number:
                    image_resample = True
                if abs(yaw) > small_number and abs(dz - dx) > small_number:
                    image_resample = True
                if abs(roll) > small_number and abs(dx - dy) > small_number:
                    image_resample = True
            if image_resample and resample not in ['coarse', 'fine']:
                resample = 'fine'

        if image_resample:
            if 'fine' == resample:
                resample_size = voxel_size_min
            elif 'coarse' == resample:
                resample_size = voxel_size_max
            else:
                resample_size = resample
            self.resample(voxel_size=resample_size, order=order)

        # Obtain rotation in radians
        pitch, yaw, roll = [math.radians(x) for x in rotation]

        # Obtain translation in pixel units
        idx, idy, idz = [translation[i] / self.voxel_size[i] for i in range(3)]

        # Obtain centre coordinates in pixel units
        xc, yc, zc = centre
        ixc = self.pos_to_idx(xc, "x", False)
        iyc = self.pos_to_idx(yc, "y", False)
        izc = self.pos_to_idx(zc, "z", False)

        # Overall transformation matrix composed from
        # individual transformations, following suggestion at:
        # https://stackoverflow.com/questions/25895587/
        #     python-skimage-transform-affinetransform-rotation-center
        # This gives control over the order in which
        # the individaul transformation are performed
        # (translation before rotation), and allows definition
        # of point about which rotation and scaling are performed.
        tf_translation = skimage.transform.SimilarityTransform(
                translation=[-idy, -idx, -idz], dimensionality=3)
        tf_centre_shift = skimage.transform.SimilarityTransform(
                translation=[-iyc, -ixc, -izc], dimensionality=3)
        tf_rotation = skimage.transform.SimilarityTransform(
                rotation=[yaw, pitch, roll], dimensionality=3)
        tf_scale = skimage.transform.SimilarityTransform(
                scale=1. / scale, dimensionality=3)
        tf_centre_shift_inv = skimage.transform.SimilarityTransform(
                translation=[iyc, ixc, izc], dimensionality=3)

        matrix = tf_translation + tf_centre_shift + tf_rotation + tf_scale \
                 + tf_centre_shift_inv

        # Set fill value
        if fill_value is None:
            fill_value = self.data.min()

        # Apply transform
        self.data = scipy.ndimage.affine_transform(self.data, matrix,
                order=order, cval=fill_value)

        # Revert to original voxel size
        if image_resample and restore:
            self.resample(voxel_size=voxel_size, order=order)

        return None

    def has_same_geometry(self, im):
        """Check whether this Image has the same geometric properties
        another Image <im> (i.e. same origin, voxel sizes, and shape)."""

        same = self.get_data().shape == im.get_data().shape
        origin1 = [f"{x:.2f}" for x in self.origin]
        origin2 = [f"{x:.2f}" for x in im.origin]
        same *= origin1 == origin2
        vx1 = [f"{x:.2f}" for x in self.voxel_size]
        vx2 = [f"{x:.2f}" for x in im.voxel_size]
        same *= vx1 == vx2
        return same



class ImageComparison(Image):
    """Plot comparisons of two images and calculate comparison metrics."""

    def __init__(self, im1, im2, plot_type="overlay", title=None, **kwargs):

        # Load images
        self.ims = []
        for im in [im1, im2]:
            if issubclass(type(im), Image):
                self.ims.append(im)
            else:
                self.ims.append(Image(im, **kwargs))

        if plot_type is not None:
            self.plot_type = plot_type
        else:
            self.plot_type = "overlay"

        self.override_title = title
        self.gs = None

    def view(self, **kwargs):
        """View self with BetterViewer."""

        from skrt.better_viewer import BetterViewer
        kwargs.setdefault("comparison", True)

        BetterViewer(self.ims, **kwargs)


    def plot(
        self,
        view="x-y",
        sl=None,
        idx=None,
        pos=None,
        scale_in_mm=True,
        invert=False,
        ax=None,
        mpl_kwargs=None,
        show=True,
        figsize=None,
        zoom=None,
        zoom_centre=None,
        plot_type=None,
        cb_splits=2,
        overlay_opacity=0.5,
        overlay_legend=False,
        overlay_legend_loc=None,
        colorbar=False,
        colorbar_label="HU",
        show_mse=False,
        dta_tolerance=None,
        dta_crit=None,
        diff_crit=None,
        use_cached_slices=False,
        **kwargs
    ):

        # Use default plot_type attribute if no type given
        if plot_type is None:
            plot_type = self.plot_type

        # By default, use comparison type as title
        if self.override_title is None:
            self.title = plot_type[0].upper() + plot_type[1:]
        else:
            self.title = self.override_title

        # Set slice inputs to lists of two items
        sl = skrt.core.to_list(sl, 2, False)
        idx = skrt.core.to_list(idx, 2, False)
        pos = skrt.core.to_list(pos, 2, False)

        # Get image slices
        idx[0] = self.ims[0].get_idx(view, sl=sl[0], idx=idx[0], pos=pos[0])
        idx[1] = self.ims[1].get_idx(view, sl=sl[1], idx=idx[1], pos=pos[1])
        self.set_slices(view, idx=idx, use_cached_slices=use_cached_slices)

        # Set up axes
        self.set_ax(view, ax=ax, gs=self.gs, figsize=figsize, zoom=zoom)
        self.mpl_kwargs = self.ims[0].get_mpl_kwargs(view, mpl_kwargs)
        self.cmap = copy.copy(matplotlib.cm.get_cmap(self.mpl_kwargs.pop("cmap")))

        # Make plot
        if plot_type in ["chequerboard", "cb"]:
            mesh = self._plot_chequerboard(invert, cb_splits)
        elif plot_type == "overlay":
            mesh = self._plot_overlay(
                invert, overlay_opacity, overlay_legend, overlay_legend_loc
            )
        elif plot_type in ["difference", "diff"]:
            mesh = self._plot_difference(invert)
        elif plot_type == "absolute difference":
            mesh = self._plot_difference(invert, ab=True)
        elif plot_type == "distance to agreement":
            mesh = self._plot_dta(view, idx, dta_tolerance)
        elif plot_type == "gamma index":
            mesh = self._plot_gamma(view, idx, invert, dta_crit, diff_crit)
        elif plot_type == "image 1":
            self.title = self.ims[0].title
            mesh = self.ax.imshow(
                self.slices[0],
                cmap=self.cmap,
                **self.mpl_kwargs,
            )
        elif plot_type == "image 2":
            self.title = self.ims[1].title
            mesh = self.ax.imshow(
                self.slices[1],
                cmap=self.cmap,
                **self.mpl_kwargs,
            )
        else:
            print("Unrecognised plotting option:", plot_type)
            return

        # Draw colorbar
        if colorbar:
            clb_label = colorbar_label
            if plot_type in ["difference", "absolute difference"]:
                clb_label += " difference"
            elif plot_type == "distance to agreement":
                clb_label = "Distance (mm)"
            elif plot_type == "gamma index":
                clb_label = "Gamma index"

            clb = self.fig.colorbar(mesh, ax=self.ax, label=clb_label)
            clb.solids.set_edgecolor("face")

        # Adjust axes
        self.label_ax(view, idx, title=self.title, **kwargs)
        self.zoom_ax(view, zoom, zoom_centre)

        # Annotate with mean squared error
        if show_mse:
            mse = np.sqrt(((self.slices[1] - self.slices[0]) ** 2).mean())
            mse_str = f"Mean sq. error = {mse:.2f}"
            if matplotlib.colors.is_color_like(show_mse):
                col = show_mse
            else:
                col = "white"
            self.ax.annotate(
                mse_str,
                xy=(0.05, 0.93),
                xycoords="axes fraction",
                color=col,
                fontsize="large",
            )

        if show:
            plt.show()

    def set_slices(self, view, sl=None, idx=None, pos=None, 
                   use_cached_slices=False):
        """Get slice of each image and set to self.slices."""

        sl = skrt.core.to_list(sl, 2, False)
        idx = skrt.core.to_list(idx, 2, False)
        pos = skrt.core.to_list(pos, 2, False)
        self.slices = [
            im.get_slice(view, sl=sl[i], pos=pos[i], idx=idx[i],
                         force=(not use_cached_slices))
            for i, im in enumerate(self.ims)]

    def _plot_chequerboard(
        self,
        invert=False,
        cb_splits=2,
    ):

        # Get masked image
        i1 = int(invert)
        i2 = 1 - i1
        size_x = int(np.ceil(self.slices[i2].shape[0] / cb_splits))
        size_y = int(np.ceil(self.slices[i2].shape[1] / cb_splits))
        cb_mask = np.kron(
            [[1, 0] * cb_splits, [0, 1] * cb_splits] * cb_splits, np.ones((size_x, size_y))
        )
        cb_mask = cb_mask[: self.slices[i2].shape[0], : self.slices[i2].shape[1]]
        to_show = {
            i1: self.slices[i1],
            i2: np.ma.masked_where(cb_mask < 0.5, self.slices[i2]),
        }

        # Plot
        for i in [i1, i2]:
            mesh = self.ax.imshow(
                to_show[i],
                cmap=self.cmap,
                **self.mpl_kwargs,
            )
        return mesh

    def _plot_overlay(
        self,
        invert=False,
        opacity=0.5,
        legend=False,
        legend_loc="auto"
    ):

        order = [0, 1] if not invert else [1, 0]
        cmaps = ["Reds", "Blues"]
        alphas = [1, opacity]
        self.ax.set_facecolor("w")
        handles = []
        for n, i in enumerate(order):

            # Show image
            mesh = self.ax.imshow(
                self.slices[i],
                cmap=cmaps[n],
                alpha=alphas[n],
                **self.mpl_kwargs,
            )

            # Make handle for legend
            if legend:
                patch_color = cmaps[n].lower()[:-1]
                alpha = 1 - opacity if alphas[n] == 1 else opacity
                handles.append(
                    mpatches.Patch(
                        color=patch_color, alpha=alpha, label=self.ims[i].title
                    )
                )

    def get_difference(self, view=None, sl=None, idx=None, pos=None, 
                       invert=False, ab=False, reset_slices=True):
        """Get array containing difference between two Images."""

        # No view/position/index/slice given: use 3D arrays
        if reset_slices and (view is None and sl is None and pos is None and idx is None):
            diff = self.ims[1].get_data() - self.ims[0].get_data()
        else:
            if view is None:
                view = "x-y"
            if reset_slices:
                self.set_slices(view, sl, idx, pos)
            diff = (
                self.slices[1] - self.slices[0]
                if not invert
                else self.slices[0] - self.slices[1]
            )
        if ab:
            diff = np.absolute(diff)
        return diff

    def _plot_difference(self, invert=False, ab=False):
        """Produce a difference plot."""

        diff = self.get_difference(reset_slices=False, ab=ab, invert=invert)
        if ab:
            min_diff = np.min(diff)
            self.mpl_kwargs["vmin"] = 0
        else:
            self.mpl_kwargs["vmin"] = np.min(diff)
        self.mpl_kwargs["vmax"] = np.max(diff)
        return self.ax.imshow(
            diff,
            cmap=self.cmap,
            **self.mpl_kwargs,
        )

    def _plot_dta(self, view, idx, tolerance=5):
        """Produce a distance-to-agreement plot."""

        dta = self.get_dta(view, idx=idx, reset_slices=False, 
                           tolerance=tolerance)
        return self.ax.imshow(
            dta,
            cmap="viridis",
            interpolation=None,
            **self.mpl_kwargs,
        )

    def _plot_gamma(self, view, idx, invert=False, dta_crit=None, 
                    diff_crit=None):
        """Produce a distance-to-agreement plot."""

        gamma = self.get_gamma(view, idx=idx, invert=invert, dta_crit=dta_crit, 
                               diff_crit=diff_crit, reset_slices=False)
        return self.ax.imshow(
            gamma,
            cmap="viridis",
            interpolation=None,
            **self.mpl_kwargs,
        )

    def get_dta(self, view="x-y", sl=None, idx=None, pos=None, tolerance=None, 
                reset_slices=True):
        """Compute distance to agreement array on current slice."""

        if not hasattr(self, "dta"):
            self.dta = {}
        if view not in self.dta:
            self.dta[view] = {}
        idx = self.ims[0].get_idx(view, sl=sl, idx=idx, pos=pos)

        if sl not in self.dta[view]:

            x_ax, y_ax = _plot_axes[view]
            vx = abs(self.ims[0].get_voxel_size()[x_ax])
            vy = abs(self.ims[0].get_voxel_size()[y_ax])

            if reset_slices:
                self.set_slices(view, sl, idx, pos)
            im1, im2 = self.slices
            if tolerance is None:
                tolerance = 5
            abs_diff = np.absolute(im2 - im1)
            agree = np.transpose(np.where(abs_diff <= tolerance))
            disagree = np.transpose(np.where(abs_diff > tolerance))
            dta = np.zeros(abs_diff.shape)
            for coords in disagree:
                dta_vec = agree - coords
                dta_val = np.sqrt(
                    vy * dta_vec[:, 0] ** 2 + vx * dta_vec[:, 1] ** 2
                ).min()
                dta[coords[0], coords[1]] = dta_val

            self.dta[view][idx] = dta

        return self.dta[view][idx]

    def get_gamma(self, view="x-y", sl=None, pos=None, idx=None, invert=False, 
                  dta_crit=None, diff_crit=None, reset_slices=True):
        """Get gamma index on current slice."""

        if reset_slices:
            self.set_slices(view, sl, idx, pos)
        im1, im2 = self.slices
        if invert:
            im1, im2 = im2, im1

        if dta_crit is None:
            dta_crit = 1
        if diff_crit is None:
            diff_crit = 15

        diff = im2 - im1
        dta = self.get_dta(view, reset_slices=False)
        return np.sqrt((dta / dta_crit) ** 2 + (diff / diff_crit) ** 2)

    def get_plot_aspect_ratio(self, *args, **kwargs):
        """Get relative width of first image."""

        return self.ims[0].get_plot_aspect_ratio(*args, **kwargs)


def load_nifti(path):
    """Load an image from a nifti file."""

    try:
        nii = nibabel.load(path)
        data = nii.get_fdata()
        affine = nii.affine
        return data, affine

    except FileNotFoundError:
        print(f"Warning: file {path} not found! Could not load nifti.")
        return None, None

    except nibabel.filebasedimages.ImageFileError:
        return None, None


def load_dicom(path):
    """Load a dicom image from one or more dicom files."""

    # Try loading single dicom file
    paths = []
    if os.path.isfile(path):
        try:
            ds = pydicom.dcmread(path, force=True)
        except pydicom.errors.InvalidDicomError:
            return None, None, None, None, None, None

        # Discard if not a valid dicom file
        if not hasattr(ds, "SOPClassUID"):
            return None, None, None, None, None, None

        # Assign TransferSyntaxUID if missing
        if not hasattr(ds, "TransferSyntaxUID"):
            ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian

        if ds.get("ImagesInAcquisition", None) == 1:
            paths = [path]

    # Case where there are multiple dicom files for this image
    if not paths:
        if os.path.isdir(path):
            dirname = path
        else:
            dirname = os.path.dirname(path)
        paths = sorted([os.path.join(dirname, p) for p in os.listdir(dirname)])

        # Ensure user-specified file is loaded first
        if path in paths:
            paths.insert(0, paths.pop(paths.index(path)))

    # Load image arrays from all files
    study_uid = None
    series_num = None
    modality = None
    orientation = None
    slice_thickness = None
    pixel_size = None
    rescale_slope = None
    rescale_intercept = None
    window_centre = None
    window_width = None
    data_slices = {}
    image_position = {}
    z_paths = {}
    for dcm in paths:
        try:

            # Load file and check it matches the others
            ds = pydicom.dcmread(dcm, force=True)
            if study_uid is None:
                study_uid = ds.StudyInstanceUID
            if series_num is None:
                series_num = ds.SeriesNumber
            if modality is None:
                modality = ds.Modality
            if orientation is None:
                orientation = ds.ImageOrientationPatient
                orient = np.array(orientation).reshape(2, 3)
                axes = [
                    sum([abs(int(orient[i, j] * j)) for j in range(3)])
                    for i in range(2)
                ]
                axes.append(3 - sum(axes))
            if (
                ds.StudyInstanceUID != study_uid
                or ds.SeriesNumber != series_num
                or ds.Modality != modality
                or ds.ImageOrientationPatient != orientation
            ):
                continue
            if not hasattr(ds, "TransferSyntaxUID"):
                ds.file_meta.TransferSyntaxUID = \
                    pydicom.uid.ImplicitVRLittleEndian

            # Get data
            pos = getattr(ds, "ImagePositionPatient", [0, 0, 0])
            z = pos[axes[2]]
            z_paths[z] = dcm
            data_slices[z] = ds.pixel_array
            image_position[z] = pos

            # Get voxel spacings
            if pixel_size is None:
                for attr in ["PixelSpacing", "ImagerPixelSpacing"]:
                    pixel_size = getattr(ds, attr, None)
                    if pixel_size:
                        break
            if slice_thickness is None:
                slice_thickness = getattr(ds, "SliceThickness", None)

            # Get rescale settings
            if rescale_slope is None:
                rescale_slope = getattr(ds, "RescaleSlope", None)
            if rescale_intercept is None:
                rescale_intercept = getattr(ds, "RescaleIntercept", None)
                if rescale_intercept is None:
                    rescale_intercept = getattr(ds, "DoseGridScaling", None)

            # Get HU window defaults
            if window_centre is None:
                window_centre = getattr(ds, "WindowCenter", None)
                if isinstance(window_centre, pydicom.multival.MultiValue):
                    window_centre = window_centre[0]
            if window_width is None:
                window_width = getattr(ds, "WindowWidth", None)
                if isinstance(window_width, pydicom.multival.MultiValue):
                    window_width = window_width[0]

        # Skip any invalid dicom files
        except pydicom.errors.InvalidDicomError:
            continue

    # Case where no data was found
    if not data_slices:
        print(f"Warning: no valid dicom files found in {path}")
        return None, None, None, None, None, None

    # Case with single image array
    if len(data_slices) == 1:
        data = list(data_slices.values())[0]

    # Combine arrays
    else:

        # Sort by slice position
        sorted_slices = sorted(list(data_slices.keys()))
        sorted_data = [data_slices[z] for z in sorted_slices]
        data = np.stack(sorted_data, axis=-1)

        # Recalculate slice thickness from spacing
        slice_thickness = (sorted_slices[-1] - sorted_slices[0]) / (
            len(sorted_slices) - 1
        )

    # Make affine matrix
    zmin = sorted_slices[0]
    zmax = sorted_slices[-1]
    n = len(sorted_slices)
    affine = np.array(
        [
            [
                orient[0, 0] * pixel_size[0],
                orient[1, 0] * pixel_size[1],
                (image_position[zmax][0] - image_position[zmin][0]) / (n - 1),
                image_position[zmin][0],
            ],
            [
                orient[0, 1] * pixel_size[0],
                orient[1, 1] * pixel_size[1],
                (image_position[zmax][1] - image_position[zmin][1]) / (n - 1),
                image_position[zmin][1],
            ],
            [
                orient[0, 2] * pixel_size[0],
                orient[1, 2] * pixel_size[1],
                (image_position[zmax][2] - image_position[zmin][2]) / (n - 1),
                image_position[zmin][2],
            ],
            [0, 0, 0, 1],
        ]
    )

    # Apply rescaling
    if rescale_slope:
        data = data * rescale_slope
    if rescale_intercept:
        data = data + rescale_intercept

    return data, affine, window_centre, window_width, ds, z_paths


def load_npy(path):
    """Load a numpy array from a .npy file."""

    try:
        data = np.load(path)
        return data

    except (IOError, ValueError):
        return


def downsample(data, dx=None, dy=None, dz=None):
    """Downsample an array by the factors specified in <dx>, <dy>, and <dz>."""

    if dx is None:
        dx = 1
    if dy is None:
        dy = 1
    if dx is None:
        dz = 1

    return data[:: round(dy), :: round(dx), :: round(dz)]


def to_inches(size):
    """Convert a size string to a size in inches. If a float is given, it will
    be returned. If a string is given, the last two characters will be used to
    determine the units:
        - 'in': inches
        - 'cm': cm
        - 'mm': mm
        - 'px': pixels
    """

    if not isinstance(size, str):
        return size

    val = float(size[:-2])
    units = size[-2:]
    inches_per_cm = 0.394
    if units == "in":
        return val
    elif units == "cm":
        return inches_per_cm * val
    elif units == "mm":
        return inches_per_cm * val / 10
    elif units == "px":
        return val / mpl.rcParams["figure.dpi"]


def write_nifti(outname, data, affine):
    """Create a nifti file at <outname> containing <data> and <affine>."""

    nii = nibabel.Nifti1Image(data, affine)
    nii.to_filename(outname)


def write_npy(outname, data, affine=None):
    """Create numpy file containing data. If <affine> is not None, voxel
    sizes and origin will be written to a text file."""

    np.save(outname, data)
    if affine is not None:
        voxel_size = np.diag(affine)[:-1]
        origin = affine[:-1, -1]
        geom_file = outname.replace(".npy", ".txt")
        with open(geom_file, "w") as f:
            f.write("voxel_size")
            for vx in voxel_size:
                f.write(" " + str(vx))
            f.write("\norigin")
            for p in origin:
                f.write(" " + str(p))
            f.write("\n")


def write_dicom(
    outdir,
    data,
    affine,
    header_source=None,
    orientation=None,
    patient_id=None,
    modality=None,
    root_uid=None,
):
    """Write image data to dicom file(s) inside <outdir>. <header_source> can
    be:
        (a) A path to a dicom file, which will be used as the header;
        (b) A path to a directory containing dicom files; the first file
        alphabetically will be used as the header;
        (c) A pydicom FileDataset object;
        (d) None, in which case a brand new dicom file with new UIDs will be
        created.
    """

    # Create directory if it doesn't exist
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    else:
        # Clear out any dicoms in the directory
        dcms = glob.glob(f"{outdir}/*.dcm")
        for dcm in dcms:
            os.remove(dcm)

    # Try loading from header
    ds = None
    if header_source:
        if isinstance(header_source, FileDataset):
            ds = header_source
        else:
            dcm_path = None
            if os.path.isfile(header_source):
                dcm_path = header_source
            elif os.path.isdir(header_source):
                dcms = glob.glob(f"{header_source}/*.dcm")
                if dcms:
                    dcm_path = dcms[0]
            if dcm_path:
                try:
                    ds = pydicom.dcmread(dcm_path, force=True)
                except pydicom.errors.InvalidDicomError:
                    pass

    # Make fresh header if needed
    fresh_header = ds is None
    if fresh_header:
        ds = create_dicom(patient_id, modality, root_uid)

    # Assign shared geometric properties
    set_dicom_geometry(ds, affine, data.shape, orientation)

    # Rescale data
    slope = getattr(ds, "RescaleSlope", 1)
    if hasattr(ds, "DoseGridScaling"):
        slope = ds.DoseGridScaling
    intercept = getattr(ds, "RescaleIntercept", 0)
    if fresh_header:
        intercept = np.min(data)
        ds.RescaleIntercept = intercept

    # Save each x-y slice to a dicom file
    for i in range(data.shape[2]):
        sl = data.shape[2] - i
        pos = affine[2, 3] + i * affine[2, 2]
        xy_slice = data[:, :, i].copy()
        xy_slice = (xy_slice - intercept) / slope
        xy_slice = xy_slice.astype(np.uint16)
        ds.PixelData = xy_slice.tobytes()
        ds.SliceLocation = pos
        ds.ImagePositionPatient[2] = pos
        outname = f"{outdir}/{sl}.dcm"
        ds.save_as(outname)

    return ds


def set_dicom_geometry(ds, affine, shape, orientation=None):

    # Set voxel sizes etc from affine matrix
    if orientation is None:
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    else:
        ds.ImageOrientationPatient = orientation
    ds.PixelSpacing = [affine[0, 0], affine[1, 1]]
    ds.SliceThickness = affine[2, 2]
    ds.ImagePositionPatient = list(affine[:-1, 3])
    ds.Columns = shape[1]
    ds.Rows = shape[0]


def create_dicom(patient_id=None, modality=None, root_uid=None):
    """Create a fresh dicom dataset. Taken from https://pydicom.github.io/pydicom/dev/auto_examples/input_output/plot_write_dicom.html#sphx-glr-auto-examples-input-output-plot-write-dicom-py."""

    # Create some temporary filenames
    suffix = ".dcm"
    filename = tempfile.NamedTemporaryFile(suffix=suffix).name

    # Populate required values for file meta information
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = "1.2.3"
    file_meta.ImplementationClassUID = "1.2.3.4"
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    # Create the FileDataset instance
    ds = FileDataset(filename, {}, file_meta=file_meta, preamble=b"\x00" * 128)

    # Add data elements
    ds.PatientID = patient_id if patient_id is not None else "123456"
    ds.PatientName = ds.PatientID
    ds.Modality = modality if modality is not None else "CT"
    ds.SeriesInstanceUID = get_new_uid(root_uid)
    ds.StudyInstanceUID = get_new_uid(root_uid)
    ds.SeriesNumber = "123456"
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0

    # Set creation date/time
    dt = datetime.datetime.now()
    ds.ContentDate = dt.strftime("%Y%m%d")
    timeStr = dt.strftime("%H%M%S.%f")  # long format with micro seconds
    ds.ContentTime = timeStr

    # Data storage
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15

    return ds


def get_new_uid(root=None):
    """Generate a globally unique identifier (GUID). Credit: Karl Harrison.

    <root> should uniquely identify the group generating the GUID. A unique
    root identifier can be obtained free of charge from Medical Connections:
        https://www.medicalconnections.co.uk/FreeUID/
    """

    if root is None:
        print(
            "Warning: using generic root UID 1.2.3.4. You should use a root "
            "UID unique to your institution. A unique root ID can be "
            "obtained free of charge from: "
            "https://www.medicalconnections.co.uk/FreeUID/"
        )
        root = "1.2.3.4"

    id1 = uuid.uuid1()
    id2 = uuid.uuid4().int & (1 << 24) - 1
    date = time.strftime("%Y%m%d")
    new_id = f"{root}.{date}.{id1.time_low}.{id2}"

    if not len(new_id) % 2:
        new_id = ".".join([new_id, str(np.random.randint(1, 9))])
    else:
        new_id = ".".join([new_id, str(np.random.randint(10, 99))])
    return new_id


def default_aspect():
    return 1


def set_ax(
    obj,
    view=None,
    ax=None,
    gs=None,
    figsize=_default_figsize,
    zoom=None,
    colorbar=False,
    aspect_getter=default_aspect,
    **kwargs,
):
    """Set up axes for plotting an object, either from a given exes or
    gridspec, or by creating new axes."""

    # Set up figure/axes
    if ax is None and gs is not None:
        ax = plt.gcf().add_subplot(gs)
    if ax is not None:
        obj.ax = ax
        obj.fig = ax.figure
    else:
        if figsize is None:
            figsize = _default_figsize
        if skrt.core.is_list(figsize):
            fig_tuple = figsize
        else:
            aspect = aspect_getter(view, zoom, colorbar, figsize)
            figsize = to_inches(figsize)
            fig_tuple = (figsize * aspect, figsize)
        obj.fig = plt.figure(figsize=fig_tuple)
        obj.ax = obj.fig.add_subplot()


def get_geometry(affine, voxel_size, origin, is_nifti=False, shape=None):
    """Get an affine matrix, voxel size list, and origin list from 
    a combination of these inputs."""

    # Get affine matrix from voxel size and origin
    if affine is None:
        
        if voxel_size is None and origin is None:
            return None, None, None

        voxel_size = list(voxel_size)
        origin = list(origin)

        affine = np.array(
            [
                [voxel_size[0], 0, 0, origin[0]],
                [0, voxel_size[1], 0, origin[1]],
                [0, 0, voxel_size[2], origin[2]],
                [0, 0, 0, 1],
            ]
        )
        if is_nifti:
            if shape is None:
                raise RuntimeError("Must provide data shape if converting "
                                   "affine matrix from nifti!")

            affine[0, :] = -affine[0, :]
            affine[1, 3] = -(
                affine[1, 3] + (shape[1] - 1) * voxel_size[1]
            )

    # Otherwise, get origin and voxel size from affine
    else:
        voxel_size = list(np.diag(affine))[:-1]
        origin = list(affine[:-1, -1])

    return affine, voxel_size, origin

