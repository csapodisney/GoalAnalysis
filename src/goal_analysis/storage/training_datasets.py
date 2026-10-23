"""Immutable content-addressed dataset files, with atomic exclusive publication."""
import os
import re
from pathlib import Path
from uuid import uuid4

from goal_analysis.normalization.training_datasets import encoded, validate


class DatasetStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise ValueError('INVALID_DATASET_ID')
        path = self.root / (identifier + '.json')
        if path.is_symlink():
            raise ValueError('DATASET_SYMLINK_NOT_ALLOWED')
        return path

    def put(self, value):
        import json

        value = validate(value)
        path = self.path(value['manifest']['dataset_id'])
        data = encoded(value) + b'\n'
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('CORRUPT_EXISTING_DATASET')
            validate(json.loads(data))
            return False
        temporary = self.root / ('.dataset-' + uuid4().hex + '.tmp')
        created_temporary = False
        try:
            with temporary.open('xb') as stream:
                created_temporary = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)  # atomic, never overwrites a winner
                return True
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError('CONFLICTING_DATASET_PUBLICATION') from None
                return False
        finally:
            if created_temporary:
                temporary.unlink(missing_ok=True)

    def read(self, identifier):
        import json

        value = validate(json.loads(self.path(identifier).read_bytes()))
        if value['manifest']['dataset_id'] != identifier:
            raise ValueError('DATASET_FILE_ID_MISMATCH')
        return value
