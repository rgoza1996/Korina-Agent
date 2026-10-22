import globals from 'globals';

export default [
  {
    files: ['Korina/js/**/*.js', 'tests/js/**/*.mjs', 'tests/browser/**/*.js', 'playwright.config.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      'no-undef': 'error',
      'no-dupe-args': 'error',
      'no-dupe-keys': 'error',
      'no-unreachable': 'error',
      'constructor-super': 'error',
      'valid-typeof': 'error',
    },
  },
];
