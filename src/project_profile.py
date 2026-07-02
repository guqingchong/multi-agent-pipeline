"""
Project Profile module to manage project-specific configurations.
"""

import os
from typing import List, Dict, Any


class ProjectProfile:
    """
    A class to represent and manage project-specific configurations.
    """
    
    def __init__(self, project_root: str = "."):
        """
        Initialize the ProjectProfile with the project root directory.
        
        Args:
            project_root: Path to the project root directory
        """
        self.project_root = project_root
        self._load_config()
    
    def _load_config(self):
        """
        Load project configuration from various sources.
        This includes detecting source extensions and test patterns.
        """
        # Default source extensions
        self.source_extensions = [
            '.py', '.js', '.ts', '.jsx', '.tsx', '.java', 
            '.cpp', '.c', '.h', '.cs', '.go', '.rb', '.php'
        ]
        
        # Default test patterns
        self.test_patterns = [
            '*test*', 'test_*', '*_test', 'spec*', '*spec',
            'conftest.py', 'test*.py', '*test.py', '*_spec.rb'
        ]
        
        # Attempt to load from a configuration file if it exists
        config_path = os.path.join(self.project_root, 'features.json')
        if os.path.exists(config_path):
            self._load_from_features_json(config_path)
        
        # Allow override from environment variables
        self._load_from_env()
    
    def _load_from_features_json(self, config_path: str):
        """
        Load configuration from features.json file.
        
        Args:
            config_path: Path to the features.json file
        """
        try:
            import json
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Update source extensions if defined in config
            if 'source_extensions' in config:
                self.source_extensions = config['source_extensions']
                
            # Update test patterns if defined in config
            if 'test_patterns' in config:
                self.test_patterns = config['test_patterns']
                
        except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
            # If config file doesn't exist or is invalid, use defaults
            pass
    
    def _load_from_env(self):
        """
        Load configuration from environment variables.
        """
        # Allow overriding source extensions from environment
        env_source_ext = os.environ.get('SOURCE_EXTENSIONS')
        if env_source_ext:
            self.source_extensions = [ext.strip() for ext in env_source_ext.split(',')]
        
        # Allow overriding test patterns from environment
        env_test_patterns = os.environ.get('TEST_PATTERNS')
        if env_test_patterns:
            self.test_patterns = [pattern.strip() for pattern in env_test_patterns.split(',')]
    
    def get_source_files(self) -> List[str]:
        """
        Get a list of all source files in the project based on extensions.
        
        Returns:
            List of source file paths
        """
        source_files = []
        for root, dirs, files in os.walk(self.project_root):
            # Skip common directories that don't contain source code
            dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'node_modules', '.venv', 'venv']]
            
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in self.source_extensions:
                    source_files.append(os.path.join(root, file))
                    
        return source_files
    
    def get_test_files(self) -> List[str]:
        """
        Get a list of all test files in the project based on patterns.
        
        Returns:
            List of test file paths
        """
        import fnmatch
        
        test_files = []
        for root, dirs, files in os.walk(self.project_root):
            # Skip common directories that don't contain source code
            dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'node_modules', '.venv', 'venv']]
            
            for file in files:
                # Check if the filename matches any test pattern
                for pattern in self.test_patterns:
                    if fnmatch.fnmatch(file, pattern):
                        test_files.append(os.path.join(root, file))
                        break  # Break to avoid duplicate entries
                        
        return test_files


# Global instance for convenience
_default_profile = None


def get_project_profile(project_root: str = ".") -> ProjectProfile:
    """
    Get the default project profile instance.
    
    Args:
        project_root: Path to the project root directory
        
    Returns:
        ProjectProfile instance
    """
    global _default_profile
    if _default_profile is None:
        _default_profile = ProjectProfile(project_root)
    elif _default_profile.project_root != project_root:
        _default_profile = ProjectProfile(project_root)
    
    return _default_profile