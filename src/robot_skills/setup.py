from setuptools import find_packages, setup

package_name = 'robot_skills'

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
    description='Executable ROS 2 skills for the robot: navigate, explore, perceive, report',
    license='MIT',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'skills_executor_node = robot_skills.skills_executor_node:main',
        ],
    },
)
