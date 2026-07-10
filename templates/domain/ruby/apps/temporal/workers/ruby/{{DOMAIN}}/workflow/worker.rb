# frozen_string_literal: true

require 'bundler/setup'
require 'temporalio/client'
require 'temporalio/worker'
require 'temporalio/worker/deployment_options'
require 'temporalio/worker_deployment_version'
require '{{DOMAIN_PKG}}_lib'
require_relative 'workflows/hello_workflow'

address = ENV.fetch('TEMPORAL_ADDRESS', 'localhost:7233')
namespace = ENV.fetch('TEMPORAL_NAMESPACE', '{{DOMAIN}}')
deployment_name = ENV.fetch('TEMPORAL_DEPLOYMENT_NAME')
build_id = ENV.fetch('TEMPORAL_WORKER_BUILD_ID')

connect_kwargs = {}
if ENV['TEMPORAL_TLS'] == 'true'
  tls_opts = {}
  cert_path = ENV['TEMPORAL_TLS_CLIENT_CERT_PATH']
  key_path = ENV['TEMPORAL_TLS_CLIENT_KEY_PATH']
  ca_path = ENV['TEMPORAL_TLS_SERVER_CA_CERT_PATH']
  tls_opts[:client_cert] = File.read(cert_path) if cert_path && !cert_path.empty?
  tls_opts[:client_private_key] = File.read(key_path) if key_path && !key_path.empty?
  tls_opts[:server_root_ca_cert] = File.read(ca_path) if ca_path && !ca_path.empty?
  connect_kwargs[:tls] = Temporalio::Client::Connection::TLSOptions.new(**tls_opts)
end

client = Temporalio::Client.connect(address, namespace, **connect_kwargs)

worker = Temporalio::Worker.new(
  client: client,
  task_queue: {{Domain}}Lib::TemporalIds::WORKFLOW_TASK_QUEUE,
  workflows: [{{Domain}}Workflow::HelloWorkflow],
  deployment_options: Temporalio::Worker::DeploymentOptions.new(
    version: Temporalio::WorkerDeploymentVersion.new(
      deployment_name: deployment_name,
      build_id: build_id
    ),
    use_worker_versioning: true
  )
)

worker.run
