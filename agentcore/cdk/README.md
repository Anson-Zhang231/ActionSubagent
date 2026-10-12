# AgentCore CDK

This small CDK app reads `agentcore.json` and `aws-targets.json`, then creates
one AgentCore stack per configured target. The AgentCore CLI runs it for
`agentcore deploy`. Add the target account and Region before deployment.
