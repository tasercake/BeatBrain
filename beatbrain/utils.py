import warnings
import enum
import numpy as np
import librosa
import imageio
import soundfile as sf
from pathlib import Path
from natsort import natsorted
from colorama import Fore
from tqdm import tqdm
from audioread.exceptions import DecodeError
import tensorflow as tf

from . import settings

warnings.simplefilter("ignore", UserWarning)
imageio.plugins.freeimage.download()


# region Data Types
class DataType(enum.Enum):
    AUDIO = 1
    NUMPY = 2
    ARRAY = 2
    IMAGE = 3
    UNKNOWN = 4
    AMBIGUOUS = 5
    ERROR = 6


EXTENSIONS = {
    DataType.AUDIO: [
        "wav",
        "flac",
        "mp3",
        "ogg",
    ],  # TODO: Remove artificial limit on supported audio formats
    DataType.NUMPY: ["npy", "npz"],
    DataType.IMAGE: ["tiff", "exr"],
}


def _decode_tensor_string(tensor):
    try:
        return tensor.numpy().decode('utf8')
    except:
        return tensor


def get_data_type(path, raise_exception=False):
    """
    Given a file or directory, return the (homogeneous) data type contained in that path.

    Args:
        path: Path at which to check the data type.
        raise_exception: Whether to raise an exception on unknown or ambiguous data types.

    Returns:
        DataType: The type of data contained at the given path (Audio, Numpy, or Image)

    Raises:
        ValueError: If `raise_exception` is True, the number of matched data types is either 0 or >1.
    """
    print(f"Checking input type(s) in {Fore.YELLOW}'{path}'{Fore.RESET}...")
    found_types = set()
    path = Path(path)
    files = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = filter(Path.is_file, path.rglob("*"))
    for f in files:
        for dtype, exts in EXTENSIONS.items():
            suffix = f.suffix[1:]
            if suffix in exts:
                found_types.add(dtype)
    if len(found_types) == 0:
        dtype = DataType.UNKNOWN
        if raise_exception:
            raise ValueError(
                f"Unknown source data type. No known file types we matched."
            )
    elif len(found_types) == 1:
        dtype = found_types.pop()
    else:
        dtype = DataType.AMBIGUOUS
        if raise_exception:
            raise ValueError(
                f"Ambiguous source data type. The following types were matched: {found_types}"
            )
    print(f"Determined input type to be {Fore.CYAN}'{dtype.name}'{Fore.RESET}")
    return dtype


# endregion

# region Helper functions
def get_paths(inp, directories=False, sort=True):
    """
    Recursively get the filenames under a given path

    Args:
        inp (str): The path to search for files under
        directories (bool): If True, return the unique parent directories of the found files
        sort (bool): Whether to sort the paths
    """
    inp = Path(inp)
    if not inp.exists():
        raise ValueError(f"Input must be a valid file or directory. Got '{inp}'")
    elif inp.is_dir():
        paths = filter(Path.is_file, inp.rglob("*"))
        if directories:
            paths = {p.parent for p in paths}  # Unique parent directories
        paths = natsorted(paths) if sort else list(paths)
    else:
        paths = [inp]
    return paths


def split_spectrogram(spec, chunk_size, truncate=True, axis=1):
    """
    Split a numpy array along the chosen axis into fixed-length chunks

    Args:
        spec (np.ndarray): The array to split along the chosen axis
        chunk_size (int): The number of elements along the chosen axis in each chunk
        truncate (bool): If True, the array is truncated such that the number of elements
                         along the chosen axis is a multiple of `chunk_size`.
                         Otherwise, the array is zero-padded to a multiple of `chunk_size`.
        axis (int): The axis along which to split the array

    Returns:
        list: A list of arrays of equal size
    """
    if spec.shape[axis] >= chunk_size:
        remainder = spec.shape[axis] % chunk_size
        if truncate:
            spec = spec[:, :-remainder]
        else:
            spec = np.pad(spec, ((0, 0), (0, chunk_size - remainder)), mode="constant")
        chunks = np.split(spec, spec.shape[axis] // chunk_size, axis=axis)
    else:
        chunks = [spec]
    return chunks


def load_image(path, flip=True):
    """
    Load an image as an array

    Args:
        path: The file to load image from
        flip (bool): Whether to flip the image vertically
    """
    path = _decode_tensor_string(path)
    spec = imageio.imread(path)
    if flip:
        spec = spec[::-1]
    return spec


def load_images(path, flip=True, concatenate=False, stack=False):
    """
    Load a sequence of spectrogram images from a directory as arrays

    Args:
        path: The directory to load images from
        flip (bool): Whether to flip the images vertically
        concatenate (bool): Whether to concatenate the loaded arrays (along axis 1)
        stack (bool): Whether to stack the loaded arrays
    """
    if concatenate and stack:
        raise ValueError("Cannot do both concatenation and stacking: choose one or neither.")
    path = _decode_tensor_string(path)
    path = Path(path)
    if path.is_file():
        files = [path]
    else:
        files = []
        for ext in EXTENSIONS[DataType.IMAGE]:
            files.extend(path.glob(f"*.{ext}"))
        files = natsorted(files)
    chunks = [load_image(file, flip=flip) for file in files]
    if concatenate:
        return np.concatenate(chunks, axis=1)
    elif stack:
        return np.stack(chunks)
    return chunks


def load_arrays(path, concatenate=False, stack=False):
    """
    Load a sequence of spectrogram arrays from a npy or npz file

    Args:
        path: The file to load arrays from
        concatenate (bool): Whether to concatenate the loaded arrays (along axis 1)
        stack (bool): Whether to stack the loaded arrays
    """
    if concatenate and stack:
        raise ValueError("Cannot do both concatenation and stacking: choose one or neither.")
    path = _decode_tensor_string(path)
    with np.load(path) as npz:
        keys = natsorted(npz.keys())
        chunks = [npz[k] for k in keys]
    if concatenate:
        return np.concatenate(chunks, axis=1)
    elif stack:
        return np.stack(chunks)
    return chunks


def audio_to_spectrogram(audio, normalize=False, norm_kwargs={}, **kwargs):
    """
    Convert an array of audio samples to a mel spectrogram

    Args:
        audio (np.ndarray): The array of audio samples to convert
        normalize (bool): Whether to log and normalize the spectrogram to [0, 1] after conversion
        norm_kwargs (dict): Additional keyword arguments to pass to the spectrogram normalization function
    """
    spec = librosa.feature.melspectrogram(audio, **kwargs)
    if normalize:
        spec = normalize_spectrogram(spec, **norm_kwargs)
    return spec


def spectrogram_to_audio(spec, denormalize=False, norm_kwargs={}, **kwargs):
    """
    Convert a mel spectrogram to audio

    Args:
        spec (np.ndarray): The mel spectrogram to convert to audio
        denormalize (bool): Whether to exp and denormalize the spectrogram before conversion
        norm_kwargs (dict): Additional keyword arguments to pass to the spectrogram denormalization function
    """
    if denormalize:
        spec = denormalize_spectrogram(spec, **norm_kwargs)
    audio = librosa.feature.inverse.mel_to_audio(spec, **kwargs)
    return audio


# TODO: Remove dependency on settings.TOP_DB
def normalize_spectrogram(spec, top_db=settings.TOP_DB, ref=np.max, **kwargs):
    """
    Log and normalize a mel spectrogram using `librosa.power_to_db()`
    """
    return (librosa.power_to_db(spec, top_db=top_db, ref=ref, **kwargs) / top_db) + 1


def denormalize_spectrogram(spec, top_db=settings.TOP_DB, ref=32768, **kwargs):
    """
    Exp and denormalize a mel spectrogram using `librosa.db_to_power()`
    """
    return librosa.db_to_power((spec - 1) * top_db, ref=ref, **kwargs)


def save_arrays(chunks, output, compress=True):
    """
    Save a sequence of arrays to a npy or npz file.

    Args:
        chunks (list): A sequence of arrays to save
        output (str): The file to save the arrays to'
        compress (bool): Whether to use `np.savez` to compress the output file
    """
    save = np.savez_compressed if compress else np.savez
    save(str(output), *chunks)


def save_image(spec, output, flip=True):
    """
    Save an array as an image.

    Args:
        spec (np.ndarray): A array to save as an image
        output (str): The path to save the image to
        flip (bool): Whether to flip the array vertically
    """
    if flip:
        spec = spec[::-1]
    imageio.imwrite(output, spec, format="exr")


def save_images(chunks, output, flip=True):
    """
    Save a sequence of arrays as images.

    Args:
        chunks (list): A sequence of arrays to save as images
        output (str): The directory to save the images to
        flip (bool): Whether to flip the images vertically
    """
    output = Path(output)
    for j, chunk in enumerate(chunks):
        save_image(chunk, output.joinpath(f"{j}.exr"), flip=flip)


# TODO: Consolidate these functions into one
def get_numpy_output_path(path, out_dir, inp):
    path = Path(path)
    out_dir = Path(out_dir)
    inp = Path(inp)
    output = out_dir.joinpath(path.relative_to(inp))
    output = output.parent.joinpath(output.stem)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def get_image_output_path(path, out_dir, inp):
    path = Path(path)
    out_dir = Path(out_dir)
    inp = Path(inp)
    output = out_dir.joinpath(path.relative_to(inp))
    output = output.parent.joinpath(output.stem)
    output.mkdir(parents=True, exist_ok=True)
    return output


def get_audio_output_path(path, out_dir, inp, fmt):
    path = Path(path)
    out_dir = Path(out_dir)
    inp = Path(inp)
    output = out_dir.joinpath(path.relative_to(inp))
    output = output.parent.joinpath(output.name).with_suffix(f".{fmt}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def load_dataset(
    path,
    flip=settings.IMAGE_FLIP,
    batch_size=settings.BATCH_SIZE,
    shuffle=True,
    shuffle_buffer=settings.SHUFFLE_BUFFER,
    prefetch=settings.DATA_PREFETCH,
    parallel=settings.DATA_PARALLEL,
    limit=None,
):
    """
    Loads a one or more images or .np{y,z} files as a `tf.data.Dataset` instance.

    Args:
        path (str): The file or directory to load data from
        flip (bool): Whether to flip loaded images
        batch_size (int):
        shuffle_buffer (int):
        prefetch (int):
        parallel (bool):
        limit (int):

    Returns:
        A `tf.data.Dataset` instance
    """
    num_parallel = tf.data.experimental.AUTOTUNE if parallel else None
    path = Path(path).resolve()
    if not path.exists():
        raise ValueError(f"Could not find '{path}'")
    dtype = get_data_type(path)
    supported_dtypes = (DataType.NUMPY, DataType.IMAGE)
    if dtype not in supported_dtypes:
        raise TypeError(f"Unsupported or ambiguous data type: {dtype}."
                        f"Must be one of {supported_dtypes}.")
    # dataset = tf.data.Dataset.list_files(f"{path}" if path.is_file() else f"{path}/**/*.*", shuffle=shuffle)
    files = []
    if path.is_file():
        files.append(path)
    elif path.is_dir():
        files.extend(get_paths(path, directories=False))
    files = [str(f) for f in files]
    dataset = tf.data.Dataset.from_tensor_slices(files)
    if shuffle_buffer:
        dataset = dataset.shuffle(shuffle_buffer)
    if dtype == DataType.IMAGE:
        dataset = dataset.map(
            lambda file: tf.py_function(load_image, [file, flip], Tout=tf.float32),
            num_parallel_calls=num_parallel,
        )
    elif dtype == DataType.NUMPY:
        dataset = dataset.map(
            lambda file: tf.py_function(load_arrays, [file, False, True], Tout=tf.float32),
            num_parallel_calls=num_parallel,
        )
        dataset = dataset.unbatch()
    dataset = dataset.map(lambda x: tf.expand_dims(x, -1), num_parallel_calls=num_parallel)
    dataset = dataset.batch(batch_size, drop_remainder=True)
    if prefetch:
        dataset = dataset.prefetch(prefetch)
    if limit:
        dataset = dataset.take(limit)
    return dataset


# endregion

# region Converters
def convert_audio_to_numpy(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        res_type=settings.RESAMPLE_TYPE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        n_mels=settings.N_MELS,
        chunk_size=settings.CHUNK_SIZE,
        truncate=settings.TRUNCATE,
        skip=0,
):
    paths = get_paths(inp, directories=False)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to Numpy arrays...")
    print(f"Arrays will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        try:
            audio, sr = librosa.load(
                str(path), sr=sr, offset=offset, duration=duration, res_type=res_type
            )
        except DecodeError as e:
            print(f"Error decoding {path}: {e}")
            continue
        spec = audio_to_spectrogram(
            audio,
            sr=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
            normalize=True,
        )
        chunks = split_spectrogram(spec, chunk_size, truncate=truncate)
        output = get_numpy_output_path(path, out_dir, inp)
        save_arrays(chunks, output)


def convert_image_to_numpy(inp, out_dir, flip=settings.IMAGE_FLIP, skip=0):
    paths = get_paths(inp, directories=True)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to Numpy arrays...")
    print(f"Arrays will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        chunks = load_images(path, flip=flip)
        output = get_numpy_output_path(path, out_dir, inp)
        save_arrays(chunks, output)


def convert_audio_to_image(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        res_type=settings.RESAMPLE_TYPE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        n_mels=settings.N_MELS,
        chunk_size=settings.CHUNK_SIZE,
        truncate=settings.TRUNCATE,
        flip=settings.IMAGE_FLIP,
        skip=0,
):
    paths = get_paths(inp, directories=False)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to images...")
    print(f"Images will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        try:
            audio, sr = librosa.load(
                str(path), sr=sr, offset=offset, duration=duration, res_type=res_type
            )
        except DecodeError as e:
            print(f"Error decoding {path}: {e}")
            continue
        spec = audio_to_spectrogram(
            audio,
            sr=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
            normalize=True,
        )
        chunks = split_spectrogram(spec, chunk_size, truncate=truncate)
        output = get_image_output_path(path, out_dir, inp)
        save_images(chunks, output, flip=flip)


def convert_numpy_to_image(inp, out_dir, flip=settings.IMAGE_FLIP, skip=0):
    paths = get_paths(inp, directories=False)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to images...")
    print(f"Images will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        chunks = load_arrays(path)
        output = get_image_output_path(path, out_dir, inp)
        save_images(chunks, output, flip=flip)


def convert_numpy_to_audio(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        fmt=settings.AUDIO_FORMAT,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        skip=0,
):
    paths = get_paths(inp, directories=False)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to audio...")
    print(f"Images will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        spec = load_arrays(path, concatenate=True)
        audio = spectrogram_to_audio(
            spec, sr=sr, n_fft=n_fft, hop_length=hop_length, denormalize=True,
        )
        output = get_audio_output_path(path, out_dir, inp, fmt)
        sf.write(output, audio, sr)


def convert_image_to_audio(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        fmt=settings.AUDIO_FORMAT,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        flip=settings.IMAGE_FLIP,
        skip=0,
):
    paths = get_paths(inp, directories=True)
    print(f"Converting files in {Fore.YELLOW}'{inp}'{Fore.RESET} to audio...")
    print(f"Images will be saved in {Fore.YELLOW}'{out_dir}'{Fore.RESET}\n")
    for i, path in enumerate(tqdm(paths, desc="Converting")):
        if i < skip:
            continue
        tqdm.write(f"Converting {Fore.YELLOW}'{path}'{Fore.RESET}...")
        spec = load_images(path, flip=flip, concatenate=True)
        audio = spectrogram_to_audio(
            spec, sr=sr, n_fft=n_fft, hop_length=hop_length, denormalize=True,
        )
        output = get_audio_output_path(path, out_dir, inp, fmt)
        sf.write(output, audio, sr)


# endregion

# region Functions used by the `click` CLI
def convert_to_numpy(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        res_type=settings.RESAMPLE_TYPE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        n_mels=settings.N_MELS,
        chunk_size=settings.CHUNK_SIZE,
        truncate=settings.TRUNCATE,
        flip=settings.IMAGE_FLIP,
        skip=0,
):
    dtype = get_data_type(inp, raise_exception=True)
    if dtype == DataType.AUDIO:
        return convert_audio_to_numpy(
            inp,
            out_dir,
            sr=sr,
            offset=offset,
            duration=duration,
            res_type=res_type,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
            chunk_size=chunk_size,
            truncate=truncate,
            skip=skip,
        )
    elif dtype == DataType.IMAGE:
        return convert_image_to_numpy(inp, out_dir, flip=flip, skip=skip)


def convert_to_image(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        res_type=settings.RESAMPLE_TYPE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        chunk_size=settings.CHUNK_SIZE,
        truncate=settings.TRUNCATE,
        flip=settings.IMAGE_FLIP,
        skip=0,
):
    dtype = get_data_type(inp, raise_exception=True)
    if dtype == DataType.AUDIO:
        return convert_audio_to_image(
            inp,
            out_dir,
            sr=sr,
            offset=offset,
            duration=duration,
            res_type=res_type,
            n_fft=n_fft,
            hop_length=hop_length,
            chunk_size=chunk_size,
            truncate=truncate,
            flip=flip,
            skip=skip,
        )
    elif dtype == DataType.NUMPY:
        return convert_numpy_to_image(inp, out_dir, flip=flip, skip=skip)


def convert_to_audio(
        inp,
        out_dir,
        sr=settings.SAMPLE_RATE,
        n_fft=settings.N_FFT,
        hop_length=settings.HOP_LENGTH,
        fmt=settings.AUDIO_FORMAT,
        offset=settings.AUDIO_OFFSET,
        duration=settings.AUDIO_DURATION,
        flip=settings.IMAGE_FLIP,
        skip=0,
):
    dtype = get_data_type(inp, raise_exception=True)
    if dtype == DataType.NUMPY:
        return convert_numpy_to_audio(
            inp,
            out_dir,
            sr=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            fmt=fmt,
            offset=offset,
            duration=duration,
            skip=skip,
        )
    elif dtype == DataType.IMAGE:
        return convert_image_to_audio(
            inp,
            out_dir,
            sr=sr,
            n_fft=n_fft,
            hop_length=hop_length,
            fmt=fmt,
            offset=offset,
            duration=duration,
            flip=flip,
            skip=skip,
        )

# endregion
