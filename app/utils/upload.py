import os
import uuid
import datetime
import hashlib
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compute_file_hash(file_storage):
    """Computes SHA-256 checksum of uploaded FileStorage object."""
    file_storage.seek(0)
    file_bytes = file_storage.read()
    file_storage.seek(0)  # Reset pointer for saving
    return hashlib.sha256(file_bytes).hexdigest()

def save_proof_file(file_storage, upload_folder, category='general'):
    """
    Saves an uploaded FileStorage object securely and returns proof metadata dictionary including SHA-256 hash.
    """
    if not file_storage or not file_storage.filename or file_storage.filename.strip() == '':
        return None
        
    if not allowed_file(file_storage.filename):
        return None

    file_hash = compute_file_hash(file_storage)
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    
    os.makedirs(upload_folder, exist_ok=True)
    
    file_path = os.path.join(upload_folder, unique_filename)
    file_storage.save(file_path)

    file_type = 'pdf' if ext == 'pdf' else 'image'

    return {
        'original_filename': filename,
        'stored_filename': unique_filename,
        'file_type': file_type,
        'category': category,
        'file_hash': file_hash,
        'ext': ext,
        'uploaded_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
