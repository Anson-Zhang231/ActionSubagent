#!/usr/bin/env node
import { AgentCoreApplication, ConfigIO } from '@aws/agentcore-cdk';
import { App, Stack } from 'aws-cdk-lib';
import * as path from 'path';

async function main(): Promise<void> {
  const config = new ConfigIO({ baseDir: path.resolve(process.cwd(), '..') });
  const spec = await config.readProjectSpec();
  const targets = await config.readAWSDeploymentTargets();
  if (targets.length === 0) {
    throw new Error('Configure an account and Region in agentcore/aws-targets.json');
  }

  const app = new App();
  for (const target of targets) {
    const stack = new Stack(app, `AgentCore-${spec.name}-${target.name}`, {
      env: { account: target.account, region: target.region },
    });
    new AgentCoreApplication(stack, 'Application', { spec });
  }
  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error);
  process.exitCode = 1;
});
