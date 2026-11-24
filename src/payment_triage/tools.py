"""Read-only tools exposed to the planner through an allowlisted gateway."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from payment_triage.models import Account, Payment
from payment_triage.repository import Repository


class ToolError(Exception):
    """A controlled tool failure that the planner may handle."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PaymentLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(min_length=1, max_length=64)


class AccountLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64)


class PolicySearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class PaymentLookupResult(BaseModel):
    payment: Payment


class AccountLookupResult(BaseModel):
    account: Account


class PolicyMatch(BaseModel):
    id: str
    title: str
    text: str
    score: int


class PolicySearchResult(BaseModel):
    matches: list[PolicyMatch]


class PolicyDoc(BaseModel):
    id: str
    title: str
    text: str
    keywords: list[str] = Field(default_factory=list)


def load_policy_docs(path: Path) -> list[PolicyDoc]:
    """Load and validate policy documents; used at startup and by the gateway."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PolicyDoc.model_validate(item) for item in data]


@dataclass
class ToolCall:
    name: str
    args_hash: str
    outcome: str
    summary: str


@dataclass
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    handler: Callable[[Any], BaseModel]
    read_only: bool = True
    description: str = ""


class ToolGateway:
    """Allowlisted, schema-validated, read-only tool access for the planner."""

    def __init__(self, repo: Repository, policy_path: Path, max_calls: int = 8) -> None:
        self._repo = repo
        self.max_calls = max_calls
        self.calls: list[ToolCall] = []
        self._policies = load_policy_docs(policy_path)
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self._register(
            ToolSpec(
                name="payment.lookup",
                args_model=PaymentLookupArgs,
                handler=self._payment_lookup,
                description="Look up a payment by ID",
            )
        )
        self._register(
            ToolSpec(
                name="account.lookup",
                args_model=AccountLookupArgs,
                handler=self._account_lookup,
                description="Look up an account by ID",
            )
        )
        self._register(
            ToolSpec(
                name="policy.search",
                args_model=PolicySearchArgs,
                handler=self._policy_search,
                description="Search synthetic policy documents",
            )
        )

    def _register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def call(self, name: str, args: dict[str, Any] | BaseModel) -> BaseModel:
        if len(self.calls) >= self.max_calls:
            raise ToolError(
                "TOOL_CALL_LIMIT", f"Maximum tool calls ({self.max_calls}) exceeded"
            )
        spec = self._tools.get(name)
        if spec is None or not spec.read_only:
            self.calls.append(
                ToolCall(
                    name,
                    _safe_args_hash(args),
                    "TOOL_NOT_ALLOWED",
                    f"Tool {name!r} is not allowlisted",
                )
            )
            raise ToolError("TOOL_NOT_ALLOWED", f"Tool {name!r} is not allowlisted")
        try:
            if isinstance(args, BaseModel):
                if not isinstance(args, spec.args_model):
                    raise ToolError(
                        "INVALID_TOOL_ARGS", f"Invalid arguments for {name}"
                    )
                validated = spec.args_model.model_validate(args.model_dump())
            else:
                validated = spec.args_model.model_validate(args)
        except ValidationError as exc:
            self.calls.append(
                ToolCall(
                    name,
                    _safe_args_hash(args),
                    "INVALID_TOOL_ARGS",
                    f"Invalid arguments for {name}",
                )
            )
            raise ToolError("INVALID_TOOL_ARGS", f"Invalid arguments for {name}") from exc
        except ToolError as exc:
            self.calls.append(
                ToolCall(name, _safe_args_hash(args), exc.code, exc.message)
            )
            raise

        args_hash = _hash_args(validated.model_dump(mode="json"))
        try:
            result = spec.handler(validated)
        except ToolError as exc:
            self.calls.append(ToolCall(name, args_hash, exc.code, exc.message))
            raise
        except Exception as exc:  # noqa: BLE001 - convert to a controlled failure
            self.calls.append(ToolCall(name, args_hash, "TOOL_ERROR", str(exc)))
            raise ToolError("TOOL_ERROR", f"Tool {name} failed") from exc

        self.calls.append(ToolCall(name, args_hash, "OK", _summarize(result)))
        return result

    def _payment_lookup(self, args: PaymentLookupArgs) -> PaymentLookupResult:
        payment = self._repo.get_payment(args.payment_id)
        if payment is None:
            raise ToolError("NOT_FOUND", f"Payment {args.payment_id} not found")
        return PaymentLookupResult(payment=payment)

    def _account_lookup(self, args: AccountLookupArgs) -> AccountLookupResult:
        account = self._repo.get_account(args.account_id)
        if account is None:
            raise ToolError("NOT_FOUND", f"Account {args.account_id} not found")
        return AccountLookupResult(account=account)

    def _policy_search(self, args: PolicySearchArgs) -> PolicySearchResult:
        tokens = _tokenize(args.query)
        matches = []
        for doc in self._policies:
            score = _score(doc, tokens)
            if score > 0:
                matches.append(
                    PolicyMatch(id=doc.id, title=doc.title, text=doc.text, score=score)
                )
        matches.sort(key=lambda match: (-match.score, match.id))
        return PolicySearchResult(matches=matches[: args.limit])

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _score(doc: PolicyDoc, tokens: set[str]) -> int:
    keyword_tokens = {keyword.lower() for keyword in doc.keywords}
    title_tokens = _tokenize(doc.title)
    text_tokens = _tokenize(doc.text)
    score = 0
    for token in tokens:
        if token in keyword_tokens:
            score += 3
        if token in title_tokens:
            score += 2
        if token in text_tokens:
            score += 1
    return score


def _hash_args(args: Any) -> str:
    payload = json.dumps(args, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _safe_args_hash(args: Any) -> str:
    try:
        if isinstance(args, BaseModel):
            payload = args.model_dump(mode="json")
        else:
            payload = args
        return _hash_args(payload)
    except Exception:  # noqa: BLE001 - hashing must never block validation
        return ""


def _summarize(result: BaseModel) -> str:
    if isinstance(result, PaymentLookupResult):
        payment = result.payment
        return (
            f"payment {payment.id} status={payment.status.value} "
            f"amount_cents={payment.amount_cents}"
        )
    if isinstance(result, AccountLookupResult):
        return f"account {result.account.id} status={result.account.status.value}"
    if isinstance(result, PolicySearchResult):
        return f"{len(result.matches)} policy matches"
    return "ok"
