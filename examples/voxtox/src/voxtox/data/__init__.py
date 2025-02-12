'''Package providing lists of datasets with outlining for VoxTox.'''

from pathlib import Path

def get_paths(ids=None, topdir='.', samples=None, pattern='VT1_*'):
    '''
    Retrieve full paths corresponding to set of identifiers.

    **Parameters:**
    ids - list, default=None
        List of patient identifiers.

    topdir - str, default='.'
        Top-level data directory.

    samples - list, default=None
        List of topdir sub-directories to be searched for patient data.

    pattern - str, default='VT1_*'
        Pattern to be matched to identify patient folders.
    '''

    # Set defaults.
    if not ids:
        ids = []
    if not samples:
        samples = ['consolidation', 'discovery',
                   'error_cases/consolidation', 'error_cases/discovery',
                   'special_cases/consolidation', 'special_cases/discovery']

    # Locate patient data.
    paths = []
    for sample in samples:
        patient_paths = (Path(topdir) / sample).glob(pattern)
        for patient_path in patient_paths:
            if patient_path.name in ids:
                paths.append(str(patient_path))

    paths.sort()
    
    return paths

def get_paths_djn_253(top_dir):
    '''
    Retrieve full paths to datasets with planning scans outlined by DJN.
    '''
    from voxtox.data.djn_253 import djn_253
    return get_paths(djn_253, top_dir)

def get_paths_jes_109(top_dir):
    '''
    Retrieve full paths to datasets with planning scans outlined by JES.
    '''
    from voxtox.data.jes_109 import jes_109
    return get_paths(jes_109, top_dir)
