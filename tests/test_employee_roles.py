import unittest

from modules.employees.roles import (
    employee_roles,
    has_any_role,
    has_role,
    primary_role,
    roles_to_storage,
)
from modules.payroll.additional_pay import can_manage_additional_pay
from modules.schedule.config import can_employee_submit_schedule, is_schedule_manager


class EmployeeRolesTests(unittest.TestCase):
    def test_legacy_single_role_is_supported(self):
        employee = {"role": "warehouse_manager", "is_active": True}
        self.assertEqual(employee_roles(employee), ["warehouse_manager"])
        self.assertTrue(has_role(employee, "warehouse_manager"))

    def test_multiple_roles_combine_capabilities(self):
        employee = {
            "role": "warehouse_manager",
            "roles": ["warehouse_manager", "admin"],
            "is_active": True,
        }
        self.assertTrue(is_schedule_manager(employee))
        self.assertTrue(can_manage_additional_pay(employee))
        self.assertTrue(has_any_role(employee, {"admin", "brand_manager"}))

    def test_employee_cannot_compose_schedule(self):
        employee = {
            "role": "warehouse_employee",
            "roles": "warehouse_employee,operations",
            "is_active": True,
        }
        self.assertFalse(can_employee_submit_schedule(employee))
        self.assertFalse(has_role(employee, "operations"))
        self.assertEqual(primary_role(employee_roles(employee)), "warehouse_employee")
        self.assertEqual(roles_to_storage(employee["roles"]), "warehouse_employee")


if __name__ == "__main__":
    unittest.main()
