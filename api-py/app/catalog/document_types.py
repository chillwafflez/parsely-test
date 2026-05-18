"""Authoritative list of Azure Document Intelligence prebuilt models this
service supports. Adding a model means appending an entry here, then (in
the frontend) adding a per-model grouping in web/lib/field-groups.ts.
Extraction, matching, and the upload picker all resolve everything they
need from this catalog by model_id — there is no other per-model code path."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentTypeDefinition:
    model_id: str
    display_name: str
    identifier_field_path: str
    flatten_maps: bool
    # True when the chosen prebuilt model returns an empty result.Tables and
    # we should run a parallel prebuilt-layout call to get visual tables.
    # Costs ~+1s and ~one extra page per upload (latency hidden by
    # asyncio.gather). False for models that include layout natively.
    needs_layout_fallback: bool
    sample_asset_url: str | None


DEFAULT_MODEL_ID = "prebuilt-invoice"


_ENTRIES: tuple[DocumentTypeDefinition, ...] = (
    DocumentTypeDefinition(
        model_id="prebuilt-invoice",
        display_name="Invoice",
        identifier_field_path="VendorName",
        flatten_maps=False,
        needs_layout_fallback=False,
        sample_asset_url=None,
    ),
    DocumentTypeDefinition(
        model_id="prebuilt-receipt",
        display_name="Receipt",
        identifier_field_path="MerchantName",
        flatten_maps=False,
        needs_layout_fallback=True,
        sample_asset_url=None,
    ),
    DocumentTypeDefinition(
        model_id="prebuilt-tax.us.w2",
        display_name="W-2",
        # The W-2 model returns Employer as a Dictionary field with Name /
        # Address / IdNumber children. flatten_maps=True surfaces them as
        # "Employer.Name" etc. so matching can target the employer name.
        identifier_field_path="Employer.Name",
        flatten_maps=True,
        needs_layout_fallback=True,
        sample_asset_url=None,
    ),
    DocumentTypeDefinition(
        model_id="prebuilt-paystub",
        display_name="Pay Stub",
        # TODO: verify against a real pay stub. The exact path
        # ("EmployerName" flat vs nested "Employer.Name") needs confirmation
        # before matching relies on it.
        identifier_field_path="EmployerName",
        flatten_maps=True,
        needs_layout_fallback=True,
        sample_asset_url=None,
    ),
    DocumentTypeDefinition(
        model_id="prebuilt-bankStatement.us",
        display_name="Bank Statement",
        # TODO: verify identifier field — likely AccountHolderName (flat)
        # or Bank.Name / Account.Name (nested). Confirm against a real
        # statement before relying on it.
        identifier_field_path="AccountHolderName",
        flatten_maps=True,
        # Bank statement was the original motivator for layout fallback: the
        # model emits Accounts/Transactions as structured fields but skips
        # result.Tables. Layout fallback recovers the full transactions table.
        needs_layout_fallback=True,
        sample_asset_url=None,
    ),
)


def all_entries() -> tuple[DocumentTypeDefinition, ...]:
    return _ENTRIES


def find(model_id: str) -> DocumentTypeDefinition | None:
    normalized = model_id.lower()
    return next((e for e in _ENTRIES if e.model_id.lower() == normalized), None)


def is_supported(model_id: str) -> bool:
    return find(model_id) is not None
