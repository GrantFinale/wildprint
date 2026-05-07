"""Admin Orders module — list, detail, refunds queue, test orders.

Phase 4b. The four endpoints registered onto ``admin_bp`` are:

* ``admin.orders_list``       — ``GET /admin/orders``
* ``admin.orders_detail``     — ``GET /admin/orders/<order_id>``
* ``admin.orders_refunds``    — ``GET /admin/orders/refunds`` (admin-only)
* ``admin.orders_test``       — ``GET /admin/orders/test`` + POST creator

Per the task spec: roles are admin/staff R+W, viewer R for list/detail; the
refunds queue is admin-only; test-order creation is admin/staff.
"""
from __future__ import annotations

from review_app.admin.orders.routes import register

__all__ = ["register"]
