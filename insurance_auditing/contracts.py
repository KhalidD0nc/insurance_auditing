from datetime import date

from .models import ContractIdentity


_CONTRACTS = {
    1: ContractIdentity(1, "H1", "INS-H1-2024-0417", date(2024, 1, 1), date(2025, 12, 31)),
    2: ContractIdentity(2, "H2", "INS-H2-2024-1183", date(2024, 1, 1), date(2025, 12, 31)),
    3: ContractIdentity(3, "H3", "INS-H3-2024-0562", date(2024, 1, 1), date(2025, 12, 31)),
    4: ContractIdentity(4, "H4", "INS-H4-2024-2049", date(2024, 1, 1), date(2025, 12, 31)),
    5: ContractIdentity(5, "H5", "INS-H5-2024-0731", date(2024, 1, 1), date(2025, 12, 31)),
}


def contract_for_hospital(hospital_number: int) -> ContractIdentity:
    try:
        return _CONTRACTS[hospital_number]
    except KeyError as exc:
        raise ValueError(f"hospital number must be between 1 and 5, got {hospital_number}") from exc
