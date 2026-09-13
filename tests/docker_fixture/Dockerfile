# Small, locally available algorithm image; no network pulls during smoke tests.
FROM algo1-4-j-76:v1
COPY interactive.py /app/manager_smoke.py
ENTRYPOINT ["python", "-u", "/app/manager_smoke.py"]
CMD []
