# frozen_string_literal: true

Gem::Specification.new do |spec|
  spec.name = '{{DOMAIN_PKG}}_lib'
  spec.version = '0.1.0'
  spec.authors = ['Temporal Demo']
  spec.summary = 'Shared Temporal IDs and activities for {{DOMAIN}}'
  spec.required_ruby_version = '>= 3.3'
  spec.files = Dir['lib/**/*.rb']
  spec.require_paths = ['lib']
  spec.add_dependency 'temporalio', '~> 1.5'
end
