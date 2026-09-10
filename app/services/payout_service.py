class PayoutService:
    @staticmethod
    def calculate_payout(estimated_repair_cost, policy_type='comprehensive', sum_insured=1500000.0):
        """
        Calculates deductible (excess) and estimated claim payout in KSh based on repair estimate and policy coverage type.
        """
        try:
            cost = float(estimated_repair_cost) if estimated_repair_cost else 0.0
        except (ValueError, TypeError):
            cost = 0.0

        try:
            sum_insured = float(sum_insured) if sum_insured else 1500000.0
        except (ValueError, TypeError):
            sum_insured = 1500000.0

        if cost <= 0:
            return {
                'estimated_repair_cost': 0.0,
                'deductible_amount': 0.0,
                'estimated_payout': 0.0,
                'coverage_notes': 'No repair cost estimated.'
            }

        policy_type = (policy_type or 'comprehensive').lower()

        # Standard Policy Excess: 5% of repair estimate, minimum KSh 10,000
        deductible = max(cost * 0.05, 10000.0)

        if policy_type == 'comprehensive':
            # Covered up to vehicle sum insured minus deductible
            coverable_amount = min(cost, sum_insured)
            payout = max(0.0, coverable_amount - deductible)
            notes = f"Comprehensive Coverage applied (5% excess = KSh {deductible:,.2f})."
        elif policy_type == 'third_party':
            # Third party pays nothing for own vehicle repairs
            payout = 0.0
            notes = f"Third-Party Liability covers 3rd party damages only. Own vehicle repair payout is KSh 0.00."
        elif policy_type == 'fire_theft':
            # Third Party, Fire & Theft
            coverable_amount = min(cost, sum_insured)
            payout = max(0.0, coverable_amount - deductible)
            notes = f"Third Party, Fire & Theft coverage applied (5% excess = KSh {deductible:,.2f})."
        else:
            coverable_amount = min(cost, sum_insured)
            payout = max(0.0, coverable_amount - deductible)
            notes = "Standard Policy Coverage applied."

        return {
            'estimated_repair_cost': round(cost, 2),
            'deductible_amount': round(deductible, 2),
            'estimated_payout': round(payout, 2),
            'coverage_notes': notes
        }
