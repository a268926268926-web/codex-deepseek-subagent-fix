# CCS Aggregation

## Core Rule

CC Switch aggregation has one default provider and any number of members. A
request with an unprefixed model goes to the default. A model with a member prefix
such as `ccs-workbuddy/<model>` goes to that member.

The default provider and member routing are separate concerns. Changing the
default changes unprefixed GPT requests, not requests explicitly bound to a
member.

## Recommended Layout

| Codex entry | Route |
| --- | --- |
| Native GPT | unchanged model name through the default provider |
| Direct DeepSeek | `cn:deepseek-v4.1-flash -> ccs-workbuddy/cn:deepseek-v4.1-flash` |

The DeepSeek route targets its member explicitly. This prevents a switch from a
GPT default provider to another provider from sending Direct DeepSeek traffic to
an upstream that does not implement it.

## Provider Compatibility

A default or member provider should satisfy all of these:

- the Codex provider uses Responses or a CCS adapter that presents Responses;
- the selected model family accepts the expected model identifiers;
- tool calls and streaming Responses survive any protocol conversion;
- V2 message delivery is supported by the relay's plaintext adaptation;
- authentication and provider-exclusive fields match the selected route.

Text-only success is insufficient. Test shell, file changes, child creation,
child wait, follow-up, and queued messages.

## Catalog Behavior

CC Switch publishes attached members as prefixed model IDs. Codex reads the model
catalog once at startup, so restart after changing the member list or default.
Models that own their own catalog can prevent aggregation rows from appearing in
ordinary CC Switch output; the launcher supplies its own catalog through
invocation-scoped app-server flags.

## Default Change Checklist

1. Select the intended member as the CCS default.
2. Confirm all required member keys remain present.
3. Confirm the direct DeepSeek route still targets `ccs-workbuddy/...`.
4. Run the launcher `--check`.
5. Restart Codex and run one native GPT request and one Direct DeepSeek request.
6. Test a V2 child for each changed principal provider.
7. Keep the direct DeepSeek route explicit during provider changes.

## Known Boundaries

- CCS aggregation does not provide failover in this mode.
- Unprefixed model names follow the current default.
- Provider pricing shown as zero for unknown models does not mean free usage.
- The relay adds a local process and a failure point.
- Provider credentials and CC Switch databases must remain private.
