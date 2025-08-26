@echo off
echo Starting personal finance automation...

cd C:\Users\jbart\AppData\Local\tmc\vscode\Truist\truist

echo Running plaid_fetch.py...
"C:\Users\jbart\AppData\Local\Programs\Python\Python313\python.exe" plaid_fetch.py >> "%USERPROFILE%\Desktop\finance_log.txt" 2>&1

echo Running parser.py with --monthly...
"C:\Users\jbart\AppData\Local\Programs\Python\Python313\python.exe" parser.py --monthly --json-only >> "%USERPROFILE%\Desktop\finance_log.txt" 2>&1

echo All done! Log saved to your desktop: finance_log.txt
pause
