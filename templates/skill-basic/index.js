// A dependency-free Skill template compatible with Vigil's Node host.
export function setup(api) {
  api.registerTool({
    id: 'echo',
    name: 'Echo',
    description: 'Return a supplied string without side effects.',
    parameters: { type: 'object', properties: { text: { type: 'string' } } },
    async execute(ctx, args) {
      return { content: String((args && args.text) || 'hello from Vigil Skill') }
    },
  })
}
