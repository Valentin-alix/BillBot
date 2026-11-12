from src.protocol import initialize_protocol
lazy from src.protocol.protocol_game import install_capture_hooks


def configure_project_import_paths() -> None:
    initialize_protocol()
    install_capture_hooks()
