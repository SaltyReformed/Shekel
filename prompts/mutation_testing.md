Set up mutation testing for the Shekel app using mutmut. Do the following:

1. Create a mutmut configuration in setup.cfg (or pyproject.toml if that 
   exists) targeting these high-risk modules first:
   - app/services/balance_calculator.py
   - app/services/paycheck_calculator.py
   - app/services/recurrence_engine.py
   - app/services/tax_calculator.py
   - app/services/interest_projection.py

2. Create a script at scripts/run_mutation_tests.py that:
   - Runs mutmut against each target module individually
   - Captures the mutation score (killed / total mutants) per module
   - Outputs a summary table to stdout
   - Writes detailed surviving mutants to docs/mutation_results.md
   - Includes docstrings and inline comments explaining each step
   - Conforms to Pylint standards with snake_case naming

3. Create a Makefile target (or add to existing if present) called 
   "mutation-test" that runs the script.

4. In docs/mutation_results.md, include a section explaining what 
   surviving mutants mean and how to interpret the results.

The mutation score target should be >= 85% for P0 services. Any module 
below 80% needs immediate attention. Run mutmut on balance_calculator.py 
as a proof of concept and include the actual results.