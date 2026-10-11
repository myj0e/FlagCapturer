import { invariant } from '../util.js';
export const s = {
  text: (maxLength = 4096, extra = {}) => ({
    type: 'string',
    maxLength,
    ...extra
  }),
  int: (minimum, maximum) => ({
    type: 'integer',
    minimum,
    maximum
  }),
  bool: () => ({
    type: 'boolean'
  }),
  enum: values => ({
    type: 'string',
    enum: values
  }),
  array: (items, maxItems = 64) => ({
    type: 'array',
    items,
    maxItems
  }),
  object: (properties = {}, required = Object.keys(properties), extra = {}) => ({
    type: 'object',
    properties,
    required,
    additionalProperties: false,
    ...extra
  })
};
export function validate(value, schema, depth = 0) {
  invariant(depth <= 12, 'Argument nesting exceeds limit');
  if (schema.enum) invariant(schema.enum.includes(value), 'Unsupported choice');
  switch (schema.type) {
    case 'string':
      invariant(typeof value === 'string' && !value.includes('\0') && value.length >= (schema.minLength ?? 0) && value.length <= (schema.maxLength ?? 65536), 'Invalid or oversized string');
      if (schema.pattern) invariant(new RegExp(schema.pattern).test(value), 'String does not match pattern');
      break;
    case 'integer':
      invariant(Number.isSafeInteger(value) && value >= schema.minimum && value <= schema.maximum, 'Integer outside range');
      break;
    case 'boolean':
      invariant(typeof value === 'boolean', 'Expected boolean');
      break;
    case 'array':
      invariant(Array.isArray(value) && value.length <= schema.maxItems && value.length >= (schema.minItems ?? 0), 'Invalid array');
      value.forEach(item => validate(item, schema.items, depth + 1));
      break;
    case 'object':
      invariant(value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length <= 128, 'Expected bounded object');
      for (const name of schema.required ?? []) invariant(Object.hasOwn(value, name), `Missing ${name}`);
      for (const [name, item] of Object.entries(value)) {
        invariant(!['__proto__', 'constructor', 'prototype'].includes(name), 'Reserved property');
        invariant(schema.additionalProperties !== false || Object.hasOwn(schema.properties, name), `Unknown property ${name}`);
        if (schema.properties?.[name]) validate(item, schema.properties[name], depth + 1);
      }
      break;
    default:
      throw new Error('Unsupported schema type');
  }
}
