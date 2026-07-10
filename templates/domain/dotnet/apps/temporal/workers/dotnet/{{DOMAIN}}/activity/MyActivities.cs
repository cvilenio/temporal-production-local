using Temporalio.Activities;

namespace {{Domain}}Demo;

public class MyActivities
{
    [Activity(TemporalIds.ActivitySayHello)]
    public Task<string> SayHelloAsync(string name) =>
        Task.FromResult($"Hello, {name}!");
}
