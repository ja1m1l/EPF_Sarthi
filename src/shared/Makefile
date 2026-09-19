build-SharedLayer:
	python -c "import os, glob, shutil; out = os.path.join(os.environ['ARTIFACTS_DIR'], 'python', 'shared'); os.makedirs(out, exist_ok=True); [shutil.copy(f, out) for f in glob.glob('*.py')]"
	python -c "import os, subprocess, sys; subprocess.run([sys.executable, '-m', 'pip', 'install', '--platform', 'manylinux2014_x86_64', '--only-binary=:all:', '--implementation', 'cp', '--python-version', '3.12', '-r', 'requirements.txt', '-t', os.path.join(os.environ['ARTIFACTS_DIR'], 'python')])"
