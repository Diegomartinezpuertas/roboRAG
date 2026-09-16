from setuptools import find_packages, setup

package_name = 'robot_rag'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='diego',
    maintainer_email='diegoamartinezpuertas@gmail.com',
    description='ChromaDB-backed semantic memory and RAG service for the robot',
    license='MIT',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'rag_node = robot_rag.rag_node:main',
            'compact_memory = robot_rag.compact_memory:main',
        ],
    },
)
