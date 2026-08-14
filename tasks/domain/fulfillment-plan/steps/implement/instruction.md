Add deterministic multi-warehouse fulfillment planning in `src/fulfillment.py`.

Preserve `can_fulfill`. Add an immutable `Shipment` value with `warehouse` and
sorted `(sku, quantity)` item pairs, plus
`plan_fulfillment(requested, warehouses)`.

`requested` maps non-empty SKU strings to positive integer quantities.
`warehouses` is a sequence of mappings with a non-empty unique `name` and a
`stock` mapping from SKU to non-negative integer quantity. Reject malformed
inputs and booleans used as integers with `ValueError`.

Return the smallest possible number of shipments whose combined stock can
fulfill the request. If several warehouse sets use that number, choose the set
whose sorted names are lexicographically first. Allocate from the selected
warehouses in name order and emit items in SKU order. Omit empty shipments.
Raise `InsufficientStock` when no plan can fulfill the request. Do not mutate
inputs. An empty request returns an empty tuple.

Only change `src/fulfillment.py`. Run the repository tests and write the
required SlopBench report with `python tools/write_slopbench_report.py`.
