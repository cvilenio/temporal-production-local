using Temporalio.Activities;

namespace {{Domain}}Demo;

public interface IHelloActivities
{
    [Activity(TemporalIds.ActivitySayHello)]
    Task<string> SayHelloAsync(string name);
}
