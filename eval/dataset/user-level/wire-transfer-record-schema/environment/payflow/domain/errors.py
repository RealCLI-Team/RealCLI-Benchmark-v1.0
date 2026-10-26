class TransferError(Exception):
    pass


class ValidationError(TransferError):
    pass


class DuplicateTransferError(TransferError):
    pass
